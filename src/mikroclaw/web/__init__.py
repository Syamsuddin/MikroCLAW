"""MikroCLAW Pulse — laman monitoring live (Fase 1: data plane + SSE).

Memakai ulang RouterOSClient untuk poll RouterOS pada cadence bertingkat,
menyimpan ring-buffer in-memory, dan mem-push state ke browser via SSE.
Belum ada lapis kecerdasan LLM (Fase 2+).
"""
