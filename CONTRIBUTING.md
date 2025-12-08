# Contributing to GrowAssistant Bridge

Thank you for your interest in contributing!

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up development environment
4. Create a branch for your changes
5. Submit a pull request

## Development Setup

### Quick Setup

```bash
git clone https://github.com/YOUR_USERNAME/GrowAssistant-Bridge.git
cd GrowAssistant-Bridge
./setup-dev.sh
```

### Manual Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp config.example.yaml config.yaml
pre-commit install
```

## Running Tests

```bash
# Run all tests with coverage
make test

# Run specific tests
pytest tests/test_config.py -v

# Check code style
make lint
```

## Code Style

We use automated tools:
- **Black**: Code formatting
- **isort**: Import sorting
- **Ruff**: Linting
- **mypy**: Type checking

Format your code before committing:

```bash
make format
```

Pre-commit hooks run automatically on each commit.

## Pull Request Process

1. Ensure tests pass: `make test`
2. Format code: `make format`
3. Update documentation if needed
4. Submit PR with clear description

### PR Checklist

- [ ] Tests pass
- [ ] Code is formatted
- [ ] Documentation updated
- [ ] No breaking changes (or documented)

## Creating Integrations

See [docs/custom_integrations.md](docs/custom_integrations.md) for detailed instructions.

Basic example:

```python
from app.integrations import Integration, register_integration

@register_integration
class MyIntegration(Integration):
    async def connect(self) -> bool:
        return True

    async def receive_data(self):
        yield {"sensor": "value"}
```

## Reporting Issues

### Bugs
- Clear description
- Steps to reproduce
- Environment details
- Log output

### Features
- Problem description
- Proposed solution
- Alternatives considered

### Security Issues
See [SECURITY.md](SECURITY.md) for reporting security vulnerabilities.

## Questions?

Open an issue with your question or check existing documentation.

Thank you for contributing!
