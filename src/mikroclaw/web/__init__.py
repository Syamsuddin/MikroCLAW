"""MikroCLAW Pulse — dashboard monitoring live.

Memakai ulang RouterOSClient untuk poll RouterOS pada cadence bertingkat,
menyimpan ring-buffer in-memory, dan mem-push state ke browser via SSE.

Tiga lapis:
  - Fase 1 (poller.py): data plane read-only + SSE.
  - Fase 2 (analyst.py): lapis AI — narasi/anomali/rekomendasi via Anthropic API.
  - Fase 3 (poller forecast + actions.py): prediksi tren deterministik +
    remediasi 1-klik yang di-gate write.
"""
