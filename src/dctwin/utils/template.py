from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

template_dir = Path(Path(__file__).parent.parent, "third_parties")
template_env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(
        default_for_string=False,
        default=False,
    ),
    trim_blocks=True,
    lstrip_blocks=True,
)
