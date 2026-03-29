# guandan-java

This directory provides a **Python bridge to the Java rule engine**: `guandan-java-action.jar` computes legal Guandan moves and related logic so you can drive environments, games, or tests from Python.

## Requirements

- **Python 3**
- **JDK** (`java` on your `PATH`)
- Place **`guandan-java-action.jar`** under this `guandan-java/` folder, or point to it with an environment variable (see below).

## Environment variables (optional)

| Variable | Description |
|----------|-------------|
| `GUANDAN_JAVA_JAR` | Full path to the JAR; if unset, defaults to `guandan-java-action.jar` in this directory. |
| `GUANDAN_JAVA_ACTION_CMD` | Custom launch command (replaces the default `java -jar ...`). |
| `GUANDAN_JAVA_MODE` | `worker` or `oneshot` (default `worker`). |
| `GUANDAN_JAVA_CACHE_SIZE` | Bridge cache size in entries (default `20000`). |

## Quick smoke test

From this directory:

```bash
python run_smoke.py
```

The script checks the JAR/Java setup and runs a short random-play smoke test.

## Layout

- `engine/` — environment wrapper and Java process bridge (legal-move parsing, etc.).
- `run_smoke.py` — smoke test entry point.
