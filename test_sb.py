import torch
import numpy as np

try:
    from speechbrain.inference.speaker import EncoderClassifier
except ImportError:
    from speechbrain.pretrained import EncoderClassifier

classifier = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device":"cpu"})
wav = torch.randn(1, 16000)
emb = classifier.encode_batch(wav)
print(emb.shape)
