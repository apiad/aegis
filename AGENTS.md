# Agents

## Running the project

```bash
aegis
```
Runs the demo server on `127.0.0.1:4243` (defined in `demo.py:main()`).

## Package management

Use `uv` (not pip):
```bash
uv pip install -e .
uv run python -c "from aegis.demo import ..."
```

## Testing

```bash
uv run pytest
```

## Key entry points

- `src/aegis/__init__.py` - exports `AegisServer`, `WorkflowContext`, `Attempt`
- `src/aegis/server.py` - core server implementation
- `src/aegis/demo.py` - workflows and prompt definitions

## Workflow definition pattern

```python
@server.workflow()
async def my_workflow(ctx: WorkflowContext):
    await ctx.step("instruction...")
    result = await ctx.step("ask for data", response_type=MyModel)
```

## Python version

Requires Python 3.13+