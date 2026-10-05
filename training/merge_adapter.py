import argparse
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    print(f"[MERGE] {args.base}")
    base = AutoModelForCausalLM.from_pretrained(
        args.base,
        dtype=torch.float16,
        device_map="cpu",
        low_cpu_mem_usage=True,
    )
    model = PeftModel.from_pretrained(base, args.adapter)
    model = model.merge_and_unload()
    Path(args.output).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output)
    tok = AutoTokenizer.from_pretrained(args.base)
    tok.save_pretrained(args.output)
    print("✓ 병합 완료")


if __name__ == "__main__":
    main()