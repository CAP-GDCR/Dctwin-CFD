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
pytest src/dctwin/runner/tests/
```

### Building Documentation

```bash
mkdocs serve
```

## Submitting Changes

1. Ensure your code passes linting (`bash lint.sh`) and tests (`pytest src/dctwin/runner/tests/`)
2. Write clear commit messages
3. Open a pull request against the `main` branch
4. Describe what your change does and why

## Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include your Python version, OpenFOAM version, and OS
- For simulation issues, include the `.cfdrun` manifest and relevant log output

## Contributor License Agreement

By submitting a contribution, you agree to the terms of [CLA.md](./CLA.md).
You represent that:

1. you have the legal right to submit the contribution;
2. the contribution is your original work, or you have sufficient rights to submit it;
3. the maintainers may use your contribution under the rights granted in the CLA, including for open source and commercial licensing.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
