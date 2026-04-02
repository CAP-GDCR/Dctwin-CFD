# Contributing to DCTwin

Thank you for your interest in contributing to DCTwin!

## Getting Started

1. Fork the repository
2. Clone your fork and set up the development environment:

```bash
git clone https://github.com/<your-username>/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

3. Create a feature branch:

```bash
git checkout -b feature/your-feature-name
```

## Development

### Code Style

We use [Black](https://black.readthedocs.io/) for formatting and [isort](https://pycqa.github.io/isort/) for import sorting:

```bash
bash lint.sh
```

### Running Tests

```bash
pytest test/
```

### Building Documentation

```bash
mkdocs serve
```

## Submitting Changes

1. Ensure your code passes linting (`bash lint.sh`) and tests (`pytest test/`)
2. Write clear commit messages
3. Open a pull request against the `main` branch
4. Describe what your change does and why

## Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include your Python version, OpenFOAM version, and OS
- For simulation issues, include the `config.toml` and relevant log output

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
