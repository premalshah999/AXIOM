"""Put pdfTeX in the bundle: the binary, and the TeX files it opens, so that resume tailoring works on a Mac with no LaTeX.

    bundle_tex.py <TeX Live root> <destination> <archive cache>      # The root holds bin/ and texmf-dist/ (TinyTeX qualifies)

What it copies comes from running the app's own renderers across every template, font and base size with `pdflatex -recorder`
and reading the .fls files, so a template that gains a package gains its files at the next build. That alone is not enough, because
the editor also takes hand-written LaTeX, which may use any shape of a font or any package installed. So it widens to:

* every file in a font directory the recorder touched, so that italic, bold and small capitals exist whichever were sampled;
* all of `tex/latex` and `tex/generic`, except hyphenation patterns the format already holds;
* Metafont and the EC and CM sources. `\\texttt` under T1 with mathptmx has no Type 1 font in this TeX, and today's compiler
  draws one with mktexpk into its scratch TEXMFVAR. The bitmaps this install has already drawn come too, to save the time.

TeX Live is relocatable by design: kpathsea looks for texmf.cnf and every tree relative to the binary ($SELFAUTOPARENT). So the
layout is kept and no environment is needed. `!!` in texmf.cnf means a tree is searched only through its ls-R, so those are written.

The editor offers five fonts, and TinyTeX has three. Charter and Palatino come from TeX Live's own package archive, pinned by
hash. They are font data that pdfTeX reads, and nothing from them is run. Without them today's app greys those two out.

The build fails unless the copy, with no PATH to anything else, compiles every combination and opens nothing outside itself.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PROGRAMS = ("pdftex", "kpsewhich", "kpseaccess", "kpsereadlink", "kpsestat", "mf-nowin", "gftopk")
# Shell scripts, reached through symlinks in bin/, that find each other by name and sit where their symlink says.
SCRIPTS = ("mktexpk", "mktextfm", "mktexmf", "mktexlsr")
NOT_WANTED = ("/doc/", "/source/", "/fonts/afm/", "/tex/generic/hyph-utf8/", "/tex/generic/hyphen/", "/fonts/opentype/")
BASE_SIZES = (10.5, 11.25, 12)  # editor.render's three base classes: 10pt, 11pt and 12pt.
ARCHIVE = "https://mirror.ctan.org/systems/texlive/tlnet/archive/{}.tar.xz"
FONT_PACKAGES = {
    "charter": "34a08f3370093966207b4a3fed029c215882c89658925624362a877f927c9bd0",
    "palatino": "a5950b7ce364231aace8830cdedbe6ab511bfe663a2f1ba0bd270e618463b6f1",
    "mathpazo": "b42822082e609bc2c707bb2657ff5bf6f491718ab391d2c884f7dfed0d51ec15",
    "fpl": "98ea140d101e0802ff266614d57f486495f1a6d063561d1474e68ac764fbc4aa",
}
# In place of the build machine's own texmf.cnf, which is personal by design: this Mac's said TEXMFAUXTREES=/Library/Frameworks/
# R.framework/..., and every compile in the installed app read R's folder (the clean-run trace, 2026-10-31).
ROOT_CNF = """% AXIOM: everything TeX reads is under this folder.
TEXMFLOCAL = $SELFAUTOPARENT/texmf-local
TEXMFHOME = $TEXMFLOCAL
TEXMFVAR = $TEXMFSYSVAR
TEXMFCONFIG = $TEXMFSYSCONFIG
TEXMFAUXTREES =
max_print_line = 10000
"""
LS_R_MAGIC = "% ls-R -- filename database for kpathsea; do not change this line.\n"


def documents():
    """Every combination the app can render, on a document using every kind of section and accented text."""
    from jobpilot.features.resumes import editor, latex

    doc = editor.ResumeDoc(
        name="Zoë Example-Müller",
        contact=["zoe@example.com", "+1 555 0100", "example.com/zoe"],
        sections=[
            editor.Section(
                id="experience",
                title="Experience",
                kind="experience",
                entries=[
                    editor.Entry(
                        id="r1",
                        heading="Staff Engineer",
                        subheading="Café Systems & Co.",
                        dates="Jan 2020 - Present",
                        location="São Paulo, Brazil",
                        bullets=[editor.Bullet(id="b1", text="Cut latency 40% for $1M accounts; shipped C++ & Rust #1 ~ _x_")],
                    )
                ],
            ),
            editor.Section(id="skills", title="Skills", kind="skills", text="Technical: Python, SQL\nLanguages: Français, Español"),
            editor.Section(id="about", title="About", kind="text", text="A plain paragraph with naïve façade."),
        ],
    )
    for template in editor.TEMPLATES:
        for font in editor.FONTS:
            for size in BASE_SIZES:
                for header, headings in (("center", "rule"), ("left", "plain")):
                    style = editor.Style(template=template, font=font, font_size=size, header=header, headings=headings)
                    yield f"{template}-{font}-{size}-{header}", _forced(editor, doc, style, font)
    # The tailored resume's own preamble (latex.py), which the editor's renderer does not share.
    yield "tailored", latex.PREAMBLE + "\\begin{document}{\\small\\itshape x}\\textbf{y} \\textbar\\end{document}\n"
    # What a person typing LaTeX by hand reaches for first.
    yield "hand-written", (
        "\\documentclass{article}\\usepackage[T1]{fontenc}\\usepackage[utf8]{inputenc}\\usepackage{mathptmx}"
        "\\usepackage{xcolor}\\usepackage{tabularx}\\begin{document}\\textsc{a}\\textsl{b}\\texttt{c}\\emph{d}\\textbf{\\textit{e}}"
        "$x^2+\\alpha$\\end{document}\n"
    )


def _forced(editor, doc, style, font):
    """editor.render falls back from a font TeX cannot load, which here would hide what a font needs."""
    known = editor._AVAILABLE
    editor._AVAILABLE = set(editor.FONTS)
    try:
        return editor.render(doc, style)
    finally:
        editor._AVAILABLE = known


def compile_all(bin_dir, tree=None):
    """{name: (ok, set of absolute paths opened)}. On the source a font it lacks just fails, and its files come another way."""
    results = {}
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C", "HOME": tempfile.gettempdir()}
    for name, tex in documents():
        with tempfile.TemporaryDirectory() as work:
            (Path(work) / "resume.tex").write_text(tex)
            env.update(TEXMFVAR=f"{work}/.v", TEXMFCONFIG=f"{work}/.c", openin_any="p", openout_any="p", shell_escape="f")
            done = subprocess.run(
                [f"{bin_dir}/pdflatex", "-recorder", "-no-shell-escape", "-interaction=batchmode", "-halt-on-error", "resume.tex"],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=120,
            )
            opened = set()
            fls = Path(work) / "resume.fls"
            if fls.exists():
                for line in fls.read_text(errors="replace").splitlines():
                    # Relative paths and what mktexpk just drew are the job's own, in its scratch folder.
                    if line.startswith("INPUT /") and not Path(line[6:]).resolve().is_relative_to(Path(work).resolve()):
                        opened.add(os.path.normpath(line[6:]))
            pdf = Path(work) / "resume.pdf"
            results[name] = (done.returncode == 0 and pdf.exists() and pdf.stat().st_size > 1000, opened)
    return results


def wanted(root, opened):
    """Relative paths to copy."""
    root = Path(root).resolve()
    chosen = set()
    for path in opened:
        real = Path(path).resolve()
        if real.is_relative_to(root):
            chosen.add(real.relative_to(root))
    # Every shape of each font family in use, whichever the samples happened to touch.
    for relative in list(chosen):
        if relative.parts[1:2] == ("fonts",) and relative.parts[2:3] != ("map",):
            for sibling in (root / relative).parent.iterdir():
                if sibling.is_file():
                    chosen.add(sibling.relative_to(root))
    for top in ("tex/latex", "tex/generic"):
        for path in (root / "texmf-dist" / top).rglob("*"):
            if path.is_file():
                chosen.add(path.relative_to(root))
    for name in ("texmf-dist/web2c/texmf.cnf", "texmf-var/web2c/pdftex/pdflatex.fmt", "texmf-var/web2c/metafont/mf.base"):
        chosen.add(Path(name))
    kept = {p for p in chosen if not any(bad in "/" + str(p) for bad in NOT_WANTED)}
    # What mktexpk needs: the scripts' own folder, Metafont's macros and the sources of the fonts it may draw.
    for top in ("texmf-dist/web2c", "texmf-dist/metafont", "texmf-dist/fonts/source/jknappen", "texmf-dist/fonts/source/public",
                "texmf-var/fonts/pk", "texmf-var/fonts/tfm"):
        for path in (root / top).rglob("*"):
            if path.is_file():
                kept.add(path.relative_to(root))
    return sorted(kept)


def add_font_packages(destination, cache):
    """Unpack the packages' fonts into the tree and add them to the font map, as updmap would. A hash that no longer matches
    (the archive moved on) fails the build rather than ship something unread: update FONT_PACKAGES after looking at it."""
    tree, maps = destination / "texmf-dist", []
    cache.mkdir(parents=True, exist_ok=True)
    for name, digest in FONT_PACKAGES.items():
        archive = cache / f"{name}.tar.xz"
        if not archive.exists():
            with urllib.request.urlopen(ARCHIVE.format(name), timeout=120) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            archive.unlink()
            raise SystemExit(f"{name}.tar.xz is not the one reviewed. Check it, then update FONT_PACKAGES in {__file__}")
        with tarfile.open(archive, "r:xz") as bundle_file:
            for member in bundle_file.getmembers():
                parts = Path(member.name).parts
                if not member.isfile() or ".." in parts or parts[0] not in ("fonts", "tex"):
                    continue
                if parts[:2] in (("fonts", "afm"), ("fonts", "source")) or member.name.endswith(".pfm"):
                    continue
                target = tree / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle_file.extractfile(member).read())
                target.chmod(0o644)
                if parts[:3] == ("fonts", "map", "dvips") and target.suffix == ".map":
                    maps.append(target)
    pdftex_map = destination / "texmf-var" / "fonts" / "map" / "pdftex" / "updmap" / "pdftex.map"
    known = {line.split()[0] for line in pdftex_map.read_text(errors="replace").splitlines() if line.strip() and not line.startswith("%")}
    with pdftex_map.open("a") as out:
        for found in maps:
            for line in found.read_text(errors="replace").splitlines():
                if line.strip() and not line.startswith("%") and line.split()[0] not in known:
                    out.write(line + "\n")


def write_ls_r(tree):
    """What `mktexlsr` writes. A tree marked !! in texmf.cnf is searched only through this."""
    tree = Path(tree)
    lines = [LS_R_MAGIC, "./:\n"]
    for folder, dirs, files in sorted(os.walk(tree)):
        relative = os.path.relpath(folder, tree)
        if relative != ".":
            lines.append(f"\n./{relative}:\n")
        lines.extend(name + "\n" for name in sorted([*dirs, *files]) if name != "ls-R")
    (tree / "ls-R").write_text("".join(lines))


def bundle(root, destination, cache):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    source_bin = root / "bin" / "universal-darwin"
    results = compile_all(source_bin)
    opened = set().union(*(paths for _, paths in results.values()))
    for relative in wanted(root, opened):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target, follow_symlinks=True)  # With its mode: mktexnam and mktexupd are run directly.
    bin_dir = destination / "bin" / "universal-darwin"
    bin_dir.mkdir(parents=True)
    for name in PROGRAMS:
        # arm64 only: the app is, and the other half is half the size. A linker-signed thin binary keeps a valid signature.
        subprocess.run(["lipo", str(source_bin / name), "-thin", "arm64", "-output", str(bin_dir / name)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(bin_dir / name)], check=True, capture_output=True)
    (destination / "texmf.cnf").write_text(ROOT_CNF)
    os.symlink("pdftex", bin_dir / "pdflatex")
    os.symlink("mf-nowin", bin_dir / "mf")
    for name in SCRIPTS:
        script = (source_bin / name).resolve()
        target = destination / script.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(script, target)
        os.chmod(target, 0o755)
        os.symlink(os.path.relpath(target, bin_dir), bin_dir / name)
    add_font_packages(destination, Path(cache))
    for tree in ("texmf-dist", "texmf-var"):
        write_ls_r(destination / tree)
    for licence in ("LICENSE.TL", "LICENSE.CTAN"):
        shutil.copy2(root / licence, destination / licence)
    # LICENSE.TL asks that a changed distribution says so, in a top-level README.<yourwork>, so nobody blames TeX Live for it.
    (destination / "README.AXIOM").write_text(
        "A subset of TeX Live, as packaged by TinyTeX, for AXIOM's resume tailoring. Files were left out, the binaries reduced to\n"
        "arm64, ls-R regenerated and a few font bitmaps kept. Nothing else was changed. Questions about this subset go to AXIOM, not\n"
        "to the TeX Live team. Each file keeps its own licence; see LICENSE.TL. The source of TeX Live is at https://tug.org/texlive/\n"
        "and https://ctan.org/, and AXIOM's maintainers will provide the source of these binaries on request.\n"
    )
    prove(results, bin_dir, destination)


def prove(source_results, bin_dir, destination):
    """The copy does everything the original did, and reaches for nothing outside itself."""
    ours = compile_all(bin_dir)
    failed = [name for name, (ok, _) in ours.items() if not ok]
    if failed:
        raise SystemExit(f"The bundled TeX failed to compile: {failed[:6]}")
    outside = sorted(p for _, paths in ours.values() for p in paths if not Path(p).resolve().is_relative_to(destination))
    if outside:
        raise SystemExit(f"The bundled TeX opened files outside itself: {outside[:6]}")
    def setting(name):
        return subprocess.run([bin_dir / "kpsewhich", f"-var-value={name}"], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True).stdout.strip()

    if Path(setting("TEXMFROOT")).resolve() != destination:
        raise SystemExit(f"The bundled TeX believes its root is {setting('TEXMFROOT')}")
    # The trees it searches, not only the files it opened: a search of someone else's folder opens nothing, and still reads it.
    trees = [t.lstrip("!") for t in setting("TEXMF").strip("{}").split(",") if t]
    strays = [t for t in trees if not Path(t).resolve().is_relative_to(destination)]
    if strays:
        raise SystemExit(f"The bundled TeX searches outside itself: {strays}")
    print(f"Compiled all {len(ours)} combinations: every template, font, base size and header, and hand-written LaTeX.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    bundle(sys.argv[1], sys.argv[2], sys.argv[3])
