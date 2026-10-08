"""Create small, reproducible prompt sets; never contacts or alters a service.

These are teaching/diagnostic texts, not an exact-token-length business replay.
Use the target tokenizer and service metrics to record actual input lengths.
"""
import argparse
import json
import random
from pathlib import Path


def write_jsonl(path, rows):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--run-id', required=True,
                        help='A new ID for each independent round; same ID for the paired on/off inputs.')
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--paragraph-repeat', type=int, default=96)
    parser.add_argument('--output-tokens', type=int, default=128)
    args = parser.parse_args()
    if min(args.count, args.paragraph_repeat, args.output_tokens) < 1:
        parser.error('count, paragraph-repeat and output-tokens must be positive')
    dest = Path(args.out_dir)
    # A fresh directory avoids mixing independent rounds or overwriting evidence.
    dest.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.run_id)
    cold, seeds, measured, warmup = [], [], [], []
    correctness, negative, expected = [], [], []
    ids = []
    for idx in range(args.count):
        ident = f'{rng.getrandbits(128):032x}'
        ids.append(ident)
        prefix = (
            f'{ident} 本轮资料编号：{args.run_id}-{idx:05d}。\n'
            + f'档案编号{ident}：这是一份用于固定请求顺序和缓存复用验证的测试资料。'
              '请保留资料中的事实，回答末尾的问题。\n' * args.paragraph_repeat
        )
        cold.append({'prompt': prefix + '问题：请简要复述本资料的目的。',
                     'output_tokens': args.output_tokens})
        seeds.append({'prompt': prefix + '预热问题：请指出资料编号。',
                      'output_tokens': 16})
        measured.append({'prompt': prefix + '正式问题：请简要复述本资料的目的。',
                         'output_tokens': args.output_tokens})
        if idx < 3:
            correctness.append({'prompt': prefix + '问题：请只回答本轮资料编号，不要解释。',
                                'output_tokens': 64})
            negid = f'{rng.getrandbits(128):032x}'
            neganswer = f'{args.run_id}-negative-{idx:05d}'
            negprefix = prefix.replace(ident, negid).replace(f'{args.run_id}-{idx:05d}', neganswer)
            negative.append({'prompt': negprefix + '问题：请只回答本轮资料编号，不要解释。',
                             'output_tokens': 64})
            expected.append({'row': idx, 'correctness_answer': f'{args.run_id}-{idx:05d}',
                             'negative_answer': neganswer})
    # Independent warmup prompts never contain any of the measured prefix IDs.
    for idx in range(5):
        ident = f'{rng.getrandbits(128):032x}'
        warmup.append({'prompt': f'{ident} 模型热身材料。' * args.paragraph_repeat
                       + f'请回答一加一等于几。热身编号{idx}。', 'output_tokens': 16})
    write_jsonl(dest / 'cold.jsonl', cold)
    write_jsonl(dest / 'warm_seed.jsonl', seeds)
    write_jsonl(dest / 'warm_measure.jsonl', measured)
    write_jsonl(dest / 'model_warmup.jsonl', warmup)
    write_jsonl(dest / 'correctness.jsonl', correctness)
    write_jsonl(dest / 'negative.jsonl', negative)
    (dest / 'expected_answers.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding='utf-8')
    (dest / 'manifest.json').write_text(json.dumps({
        'run_id': args.run_id, 'count': args.count,
        'paragraph_repeat': args.paragraph_repeat,
        'requested_output_tokens': args.output_tokens,
        'prefix_ids': ids,
        'format': 'vLLM custom JSONL; raw user text for openai-chat with skip-chat-template',
        'input_length': 'Not an exact token target. Measure using the actual chat template/tokenizer.',
        'state_warning': 'Use separate run IDs for cold and warm experiments. Generation does not prepare caches.',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Created {dest}; {args.count} rows in each measured dataset.')
    print('No requests were sent. Actual input token lengths and cache state must be verified.')


if __name__ == '__main__':
    main()
