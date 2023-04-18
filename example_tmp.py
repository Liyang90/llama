# Copyright (c) Meta Platforms, Inc. and affiliates.
# This software may be used and distributed according to the terms of the GNU General Public License version 3.

from typing import Tuple
import os
import sys
import torch
import fire
import time
# import torch_xla.core.xla_model as xm
# import torch_xla.debug.metrics as met
# import torch_xla.debug.profiler as xp
# import torch_xla.distributed.xla_multiprocessing as xmp
import json
from pathlib import Path

from llama import ModelArgs, Transformer, Tokenizer, LLaMA
from llama.xla_model_parallel import set_g_group

import torch.distributed as dist
import torch.multiprocessing as xmp

def setup_model_parallel(rank, world_size) -> Tuple[int, int]:
    # assuming model parallelism over the whole world size
    os.environ['MASTER_ADDR'] = 'localhost'
    os.environ['MASTER_PORT'] = '12355'

    # initialize the process group
    dist.init_process_group("nccl", rank=rank, world_size=world_size)
    set_g_group()

    # seed must be the same in all processes
    torch.manual_seed(1)


def init(
    tokenizer_path: str,
    rank: int,
    world_size: int,
    max_seq_len: int,
    max_batch_size: int,
    dim: int = 4096,
    n_layers: int = 32,
    n_heads: int = 32,
) -> LLaMA:
    start_time = time.time()
    # checkpoints = sorted(Path(ckpt_dir).glob("*.pth"))
    # assert world_size == len(
    #     checkpoints
    # ), f"Loading a checkpoint for MP={len(checkpoints)} but world size is {world_size}"
    # ckpt_path = checkpoints[rank]
    print("Loading")
    # checkpoint = torch.load(ckpt_path, map_location="cpu")
    # with open(Path(ckpt_dir) / "params.json", "r") as f:
    #     params = json.loads(f.read())
    params = {"dim": dim,
              "n_layers": n_layers,
              "n_heads": n_heads,
              }
    model_args: ModelArgs = ModelArgs(
        max_seq_len=max_seq_len, max_batch_size=max_batch_size, **params
    )
    tokenizer = Tokenizer(model_path=tokenizer_path)
    model_args.vocab_size = tokenizer.n_words
    # torch.set_default_tensor_type(torch.cuda.HalfTensor)  # TODO: this line puts the model to cuda device
    torch.set_default_tensor_type(torch.BFloat16Tensor)
    model = Transformer(model_args)
    # device = xm.xla_device()
    device = f"cuda:{rank}"
    model = model.to(device)
    for i in range(len(model.cache_kvs)):
        model.cache_kvs[i] = tuple(t.to(device) for t in model.cache_kvs[i])
    torch.set_default_tensor_type(torch.FloatTensor)
    # model.load_state_dict(checkpoint, strict=False)

    generator = LLaMA(model, tokenizer)
    print(f"Loaded in {time.time() - start_time:.2f} seconds")
    return generator


def main(
    rank: int,
    world_size: int,
    tokenizer_path: str,
    temperature: float = 0.8,
    top_p: float = 0.95,
    max_seq_len: int = 512,
    max_batch_size: int = 32,
    dim: int = 4096,
    n_layers: int = 32,
    n_heads: int = 32,
):
    # server = xp.start_server(9012, only_on_master=False)
    setup_model_parallel(rank, world_size)
    if rank > 0:
        sys.stdout = open(os.devnull, "w")

    generator = init(
        tokenizer_path, rank, world_size, max_seq_len, max_batch_size, dim, n_layers, n_heads
    )

    prompts = [
        # For these prompts, the expected answer is the natural continuation of the prompt
        "I believe the meaning of life is",
        # "Simply put, the theory of relativity states that ",
        # "Building a website can be done in 10 simple steps:\n",
        # Few shot prompts: https://huggingface.co/blog/few-shot-learning-gpt-neo-and-inference-api
#        """Tweet: "I hate it when my phone battery dies."
#Sentiment: Negative
####
#Tweet: "My day has been 👍"
#Sentiment: Positive
####
#Tweet: "This is the link to the article"
#Sentiment: Neutral
####
#Tweet: "This new music video was incredibile"
#Sentiment:""",
#        """Translate English to French:
#
#sea otter => loutre de mer
#
#peppermint => menthe poivrée
#
#plush girafe => girafe peluche
#
#cheese =>""",
    ]
    for i in range(2):
        with torch.no_grad():
            results = generator.generate(
                prompts, max_gen_len=256, temperature=temperature, top_p=top_p
            )

        for result in results:
            print(result)
            print("\n==================================\n")


def _fn(
    rank: int,
    world_size: int,
    tokenizer_path: str,
    temperature: float = 0.8,
    top_p: float = 0.95,
    max_seq_len: int = 512,
    max_batch_size: int = 32,
    dim: int = 4096,
    n_layers: int = 32,
    n_heads: int = 32,
):
    main(rank, world_size, tokenizer_path, temperature, top_p, max_seq_len, max_batch_size, dim, n_layers, n_heads)

def mp_main(
    mp: bool,
    tokenizer_path: str,
    temperature: float = 0.8,
    top_p: float = 0.95,
    max_seq_len: int = 512,
    max_batch_size: int = 32,
    dim: int = 4096,
    n_layers: int = 32,
    n_heads: int = 32,
):
    world_size = 1
    assert torch.cuda.is_available(), "cuda is not available"
    if mp:
        world_size = torch.cuda.device_count()
        print(f"Spawning {world_size} processes")
        xmp.spawn(_fn, args=(world_size, tokenizer_path, temperature, top_p, max_seq_len, max_batch_size, dim, n_layers, n_heads), nprocs=world_size, join=True)
    else:
        main(0, world_size, tokenizer_path, temperature, top_p, max_seq_len, max_batch_size, dim, n_layers, n_heads)


if __name__ == "__main__":
    fire.Fire(mp_main)
    # print(met.metrics_report())
