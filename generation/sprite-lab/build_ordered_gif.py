"""Build one GIF by concatenating approved direction GIF frame sequences."""
from __future__ import annotations

import argparse
from pathlib import Path

import sprite_render


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--order", nargs="+", type=int, default=[1, 2, 5, 4, 3, 8, 7, 6])
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if len(args.order) != args.rows or sorted(args.order) != list(range(1, args.rows + 1)):
        raise ValueError("order deve conter cada direção exatamente uma vez")
    frames = [
        args.source / f"row{direction - 1}_col{phase}.png"
        for direction in args.order
        for phase in range(args.phases)
    ]
    missing = [path.name for path in frames if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"frames ausentes: {missing}")
    path = sprite_render._write_gif(frames, args.output, args.fps)
    if path is None:
        raise RuntimeError("não foi possível gerar o GIF")
    print(path)


if __name__ == "__main__":
    main()
