#!/bin/bash
# download_models.sh - GGUF 모델 다운로드 스크립트

set -e

MODELS_DIR="/pyovis_memory/models"
mkdir -p "$MODELS_DIR"

echo "Downloading GLM-4.7-Flash-Q4_K_M.gguf..."
curl -L -C - -o "$MODELS_DIR/GLM-4.7-Flash-Q4_K_M.gguf" \
  "https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/resolve/main/GLM-4.7-Flash-Q4_K_M.gguf"

echo "Downloading Qwen3-14B-Q5_K_M.gguf..."
curl -L -C - -o "$MODELS_DIR/Qwen3-14B-Q5_K_M.gguf" \
  "https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q5_K_M.gguf"

echo "Downloading Devstral-24B Q4_K_M..."
curl -L -C - -o "$MODELS_DIR/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf" \
  "https://huggingface.co/bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF/resolve/main/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"

echo "Downloading DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf..."
curl -L -C - -o "$MODELS_DIR/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf" \
  "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"

echo "Done! Models downloaded to $MODELS_DIR"
ls -lh "$MODELS_DIR"
