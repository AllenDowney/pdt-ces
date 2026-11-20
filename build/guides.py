"""Parsers for the CES guides' text (pdftotext -layout). Layout is stripped, never content."""

import re

PAGE_HEADER = re.compile(r"^\S.*\S\s{5,}\d+\s*$")      # "Demographics          15"
BULLET = re.compile(r"^\s*–\s+")


def unwrap(lines):
    return " ".join(" ".join(l.split()) for l in lines if l.strip()).strip()


def cumulative_entries(text, names):
    """{name: {"title", "question", "description", "years", "notes"}} from the cumulative guide.

    An entry starts at a line "name: Title". Its wording is in quotes, or a description
    in [brackets]; the guide says brackets mark summaries rather than verbatim text.
    The bullets after the tabulation give the years and the limitations.
    """
    lines = [l for l in text.splitlines() if not PAGE_HEADER.match(l) and "\f" not in l[:1]]
    lines = [l.replace("\f", "") for l in text.splitlines()]
    lines = [l for l in lines if not PAGE_HEADER.match(l)]
    starts = {}
    for i, line in enumerate(lines):
        m = re.match(r"^(\w+): (\S.*)$", line)
        if m and m.group(1) in names and m.group(1) not in starts and not re.search(r"\.\s\.\s\.", line):
            starts[m.group(1)] = (i, m.group(2).strip())
    heads = sorted(i for i, _ in starts.values())
    out = {}
    for name, (i, title) in starts.items():
        nxt = min([h for h in heads if h > i] + [len(lines)])
        # the next entry of any variable, not just the slice's, ends this one
        for j in range(i + 1, nxt):
            if re.match(r"^\w+: \S", lines[j]) and not BULLET.match(lines[j]):
                nxt = j
                break
        body = lines[i + 1:nxt]
        # first paragraph: the wording or the description
        k = 0
        while k < len(body) and not body[k].strip():
            k += 1
        para = []
        while k < len(body) and body[k].strip():
            para.append(body[k])
            k += 1
        first = unwrap(para)
        entry = {"title": title}
        if first.startswith("“") or first.startswith('"'):
            entry["question"] = first
        elif first.startswith("["):
            entry["description"] = first
        # bullets
        bullets, cur = [], None
        for line in body[k:]:
            if BULLET.match(line):
                if cur is not None:
                    bullets.append(cur)
                cur = [BULLET.sub("", line)]
            elif cur is not None and line.strip() and line.startswith("     "):
                cur.append(line)
            elif cur is not None and not line.strip():
                bullets.append(cur)
                cur = None
        if cur is not None:
            bullets.append(cur)
        notes = []
        for b in bullets:
            t = unwrap(b)
            if t.startswith("Years:"):
                entry["years"] = t[len("Years:"):].strip()
            else:
                notes.append(t)
        if notes:
            entry["notes"] = notes
        out[name] = entry
    return out


def yearly_entries(text, names):
    """{name: {"label", "question", "location"}} from a yearly guide's codebook section.

    A variable prints as its label, its question, then a line holding only its name,
    then a tabulation. A grid prints the stem once over a line "<stem>grid", and each
    item's tabulation header ends "-- <item>"; item a is the first.
    """
    lines, page = [], []
    for number, text_of_page in enumerate(text.split("\f"), 1):
        for line in text_of_page.splitlines() + [""]:   # a page break ends a block
            lines.append(line)
            page.append(number)
    # A table's header can spill into the block ("...born?      All", "   Birth Year").
    lines = [re.sub(r"\s{5,}All\s*$", "", l) for l in lines]
    kept = [k for k, l in enumerate(lines) if not re.match(r"^\s{20,}\S", l)]
    lines = [lines[k] for k in kept]
    index = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if re.fullmatch(r"[A-Za-z_][\w]*", s):
            # The name line is the one after its block. A label can be the bare name too
            # (2020 "hispanic"), so keep the occurrence with the longest block above it.
            above = 0
            while i - above - 1 >= 0 and lines[i - above - 1].strip():
                above += 1
            if s not in index or above > index[s][1]:
                index[s] = (i, above)
    index = {k: i for k, (i, _) in index.items()}
    out = {}
    for name in names:
        grid = None
        if name in index:
            i = index[name]
        else:
            m = re.fullmatch(r"(CC\d\d_\d+)([a-z])", name)
            if not m or m.group(1) + "grid" not in index:
                continue
            i = index[m.group(1) + "grid"]
            grid = ord(m.group(2)) - ord("a")
        j = i - 1
        block = []
        while j >= 0 and lines[j].strip():
            block.insert(0, lines[j])
            j -= 1
        if not block:
            continue
        entry = {"location": f"p. {page[kept[i]]}"}
        # A block is a short label and the question, or the question alone (2022 newsint).
        if len(block) > 1 and len(block[0].strip()) < 60:
            entry["label"] = " ".join(block[0].split())
            entry["question"] = unwrap(block[1:])
        else:
            entry["question"] = unwrap(block)
        if grid is not None:
            headers = []
            for line in lines[i + 1:i + 400]:
                if " -- " in line:
                    headers.append(line)
                elif re.fullmatch(r"\s*\S+\s*", line) and headers and line.strip().endswith("grid"):
                    break
            if grid < len(headers):
                item = re.sub(r"\s+All\s*$", "", headers[grid]).split(" -- ", 1)[1].strip()
                entry["item"] = item
                entry["question"] = (entry.get("question", "") + " -- " + item).strip()
        out[name] = entry
    return out
