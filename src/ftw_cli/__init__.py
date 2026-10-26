from importlib.metadata import version

__version__ = version("ftw-cli")
# Nominatim's usage policy asks for an identifying User-Agent; Source Coop refuses urllib's.
USER_AGENT = f"ftw-cli/{__version__} (https://github.com/fieldsoftheworld/cli)"
