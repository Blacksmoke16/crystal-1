import collections
import xml.etree.ElementTree as etree
from typing import List, TypeVar

from markdown import Markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup
from mkdocstrings.handlers.base import BaseRenderer, CollectionError

from .collector import CrystalCollector, DocObject

T = TypeVar("T")


class CrystalRenderer(BaseRenderer):
    fallback_theme = "material"

    default_config: dict = {
        "show_source": True,
        "heading_level": 2,
        "deduplicate_toc": True,
    }

    def render(self, data: DocObject, config: dict) -> str:
        econfig = collections.ChainMap(config, self.default_config)

        template = self.env.get_template(f"{data.JSON_KEY.rstrip('s')}.html")

        return template.render(
            config=econfig,
            obj=data,
            heading_level=econfig["heading_level"],
            root=True,
        )

    def update_env(self, md: Markdown, config: dict) -> None:
        econfig = collections.ChainMap(self.default_config)
        try:
            econfig = econfig.new_child(config["mkdocstrings"]["handlers"]["crystal"]["rendering"])
        except KeyError:
            pass

        if md != getattr(self, "_prev_md", None):
            self._prev_md = md

            if econfig["deduplicate_toc"]:
                DeduplicateTocExtension().extendMarkdown(md)

            extensions = list(config["mdx"])
            extensions.append(_EscapeHtmlExtension())
            extensions.append(XrefExtension(self.collector))
            self._md = Markdown(extensions=extensions, extension_configs=config["mdx_configs"])

        super().update_env(self._md, config)
        self.env.trim_blocks = True
        self.env.lstrip_blocks = True
        self.env.keep_trailing_newline = False

        self.env.filters["convert_markdown"] = self._convert_markdown

    def _convert_markdown(self, text: str, context: DocObject):
        self._md.treeprocessors["mkdocstrings_crystal_xref"].context = context
        return Markup(self._md.convert(text))


class _EscapeHtmlExtension(Extension):
    def extendMarkdown(self, md: Markdown):
        del md.preprocessors["html_block"]
        del md.inlinePatterns["html"]


class XrefExtension(Extension):
    def __init__(self, collector: CrystalCollector, **kwargs) -> None:
        super().__init__(**kwargs)
        self.collector = collector

    def extendMarkdown(self, md: Markdown) -> None:
        md.registerExtension(self)
        md.treeprocessors.register(
            _RefInsertingTreeprocessor(md, self.collector), "mkdocstrings_crystal_xref", 12
        )


class _RefInsertingTreeprocessor(Treeprocessor):
    def __init__(self, md, collector: CrystalCollector):
        super().__init__(md)
        self.collector = collector

    def run(self, root: etree.Element):
        for i, el in enumerate(root):
            if el.tag != "code":
                self.run(el)
                continue

            try:
                ref_obj = self.collector.collect("".join(el.itertext()), {}, context=self.context)
            except CollectionError:
                continue

            # Replace the `code` with a new `span` (need to propagate the tail too).
            root[i] = span = etree.Element("span")
            span.tail = el.tail
            el.tail = None
            # Put the `code` into the `span`, with a special attribute for mkdocstrings to pick up.
            span.append(el)
            span.set("data-mkdocstrings-identifier", ref_obj.abs_id)


def _deduplicate_toc(toc: List[dict]) -> None:
    i = 0
    while i < len(toc):
        el = toc[i]
        if el.get("children"):
            _deduplicate_toc(el["children"])
        elif i > 0 and el["name"] == toc[i - 1]["name"]:
            del toc[i]
            continue
        i += 1


class _TocDeduplicatingTreeprocessor(Treeprocessor):
    def run(self, root: etree.Element):
        try:
            toc = self.md.toc_tokens
        except AttributeError:
            return
        _deduplicate_toc(toc)


class DeduplicateTocExtension(Extension):
    def extendMarkdown(self, md: Markdown) -> None:
        md.registerExtension(self)
        md.treeprocessors.register(
            _TocDeduplicatingTreeprocessor(md), "mkdocstrings_crystal_deduplicate_toc", 4
        )