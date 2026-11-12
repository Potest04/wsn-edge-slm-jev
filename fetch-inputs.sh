#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p .cache/llama

fetch() {
  local url=$1 path=$2 expected=$3
  if [[ -f "$path" ]] && printf '%s  %s\n' "$expected" "$path" | sha256sum --check --status; then
    return
  fi
  curl --fail --location --silent --show-error --retry 2 --max-time 600 "$url" -o "$path.part"
  printf '%s  %s\n' "$expected" "$path.part" | sha256sum --check
  mv -- "$path.part" "$path"
}

fetch https://db.csail.mit.edu/labdata/data.txt.gz .cache/intel-lab-data.txt.gz \
  d99288c8f406ca6604d359ceaa0d8adfffa79e7095061a1e27dc4399f48c7225
fetch https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf .cache/qwen.gguf \
  74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db
fetch https://github.com/ggml-org/llama.cpp/releases/download/b12318/llama-b12318-bin-ubuntu-x64.tar.gz .cache/llama-b12318.tar.gz \
  092ee3cb21574ff3a451557f7dd45a815a5c8fd1bc62923b48c916d04c5e4443
tar -xzf .cache/llama-b12318.tar.gz -C .cache/llama
