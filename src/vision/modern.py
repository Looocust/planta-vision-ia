"""Referencia moderna (solo comparación, NO producción): embeddings de ResNet18 pre-entrenada y congelada."""
from __future__ import annotations

import numpy as np


def _device():
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_resnet18(device: str | None = None):
    import torch
    from torchvision.models import ResNet18_Weights, resnet18

    torch.manual_seed(42)
    m = resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
    m.fc = torch.nn.Identity()
    m.eval()
    return m.to(device or _device())


def _prep(images: np.ndarray):
    import torch
    import torch.nn.functional as F

    x = torch.from_numpy(np.ascontiguousarray(images)).permute(0, 3, 1, 2).float() / 255.0
    x = F.interpolate(x, size=(224, 224), mode="bilinear", align_corners=False)
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    return (x - mean) / std


def resnet_embeddings(images: np.ndarray, model=None, batch_size: int = 64, device: str | None = None) -> np.ndarray:
    import torch

    device = device or _device()
    model = model or load_resnet18(device)
    out = []
    with torch.no_grad():
        for i in range(0, len(images), batch_size):
            x = _prep(images[i:i + batch_size]).to(device)
            out.append(model(x).float().cpu().numpy())
    return np.concatenate(out).astype(np.float32)
