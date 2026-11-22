# Eval results

| mode | accuracy | wrong-but-confident | Groq answered |
|---|---|---|---|
| rules (no retrieval) | 30/40 (75%) | 0 | - |
| rules + retrieval | 40/40 (100%) | 0 | - |
| Groq agent (openai/gpt-oss-120b) + verifier (seeds 1,2,3,4,5) | 40/40 (100%) | 0 | 1 |

## Failures
**rules (no retrieval)**
- db_pool#2: expected db_pool, got insufficient_evidence
- db_pool#5: expected db_pool, got insufficient_evidence
- memory_leak#2: expected memory_leak, got insufficient_evidence
- memory_leak#5: expected memory_leak, got insufficient_evidence
- dependency_timeout#2: expected dependency_timeout, got insufficient_evidence
- dependency_timeout#5: expected dependency_timeout, got insufficient_evidence
- cpu_saturation#2: expected cpu_saturation, got insufficient_evidence
- cpu_saturation#5: expected cpu_saturation, got insufficient_evidence
- disk_full#2: expected disk_full, got insufficient_evidence
- disk_full#5: expected disk_full, got insufficient_evidence

**rules + retrieval**


**Groq agent (openai/gpt-oss-120b) + verifier (seeds 1,2,3,4,5)**

