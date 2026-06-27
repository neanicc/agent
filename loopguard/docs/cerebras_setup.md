# Cerebras setup

Cerebras support is optional.

```bash
export CEREBRAS_API_KEY="..."
export CEREBRAS_MODEL="gpt-oss-120b"
pip install -e ".[dev,cerebras]"
loopguard demo --scenario cerebras
```

If no key is present, the demo exits gracefully. Core tests and local demos do not use Cerebras.
