"""Build the Cooperative Election Study (CES) metadata records from the guides and the data files.

Usage:
    python build/build_records.py [release ...]     # under the memory cap (CLAUDE.md)

Reads build/slice.yaml and the sources in data/raw/ (sources.yaml). Writes
metadata/releases/<release>/<table>/variables.jsonl, metadata/links.jsonl and build/report.md.

Where each field comes from (STANDARD.md §3):
- label, value labels, storage type, which codes occur and their counts: the Stata files
- question text, description and notes: the guides (build/guides.py)
- measure: build/slice.yaml (hand)
- missing codes: the cumulative file stores missing as Stata's system missing (.), which its
  guide prints as "(Missing)" without saying why (rule ces_system_missing). In a yearly file,
  system missing means skipped or not asked, as its guide says (Part III's introduction).
  Labelled codes such as "Not sure" are typed by rule ces_missing_from_label.
- links: the cumulative file's build code (derived_from, with the relations its renames and
  recodes imply, and the year it holds for in `waves`), cited by file and quoted in `note`;
  same_item between consecutive yearly releases by rule, from the same documentation or from
  the build code mapping both items unchanged to one cumulative variable.
"""

import collections
import json
import re
import sys
from pathlib import Path

import pyreadstat
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
META = ROOT / "metadata"
DATASET = "ces"
sys.path.insert(0, str(ROOT / "build"))
from guides import cumulative_entries, yearly_entries  # noqa: E402

RULE_VERSION = "0.1"
AUTHOR = "Claude (PublicDataToolkit Task 59)"
DATE = "2026-11-20"

RELEASES = {
    "cumulative": {"table": "cumulative", "data": "cumulative-dta", "guide": "cumulative-guide",
                   "dta": "cumulative_2006-2025.dta", "guide_txt": "guide_cumulative_2006-2025.txt"},
    "2020": {"table": "common", "data": "ces2020-dta", "guide": "ces2020-guide",
             "dta": "CES20_Common_OUTPUT_vv.dta", "guide_txt": "CCES_Guide_2020.txt"},
    "2022": {"table": "common", "data": "ces2022-dta", "guide": "ces2022-guide",
             "dta": "CCES22_Common_OUTPUT_vv_topost.dta", "guide_txt": "CES_Guide_2022.txt"},
    "2024": {"table": "common", "data": "ces2024-dta", "guide": "ces2024-guide",
             "dta": "CCES24_Common_OUTPUT_vv_topost_final.dta", "guide_txt": "CES_2024_GUIDE_vv.txt"},
}
BUILD_CODE = {"03_read-common.R": "cumulative-build-read", "05_functions-stack.R": "cumulative-build-functions",
              "05_stack-cumulative.R": "cumulative-build-stack"}

# Rule ces_missing_from_label: a code is missing when its whole label says why there is no
# substantive answer. The CES never marks codes missing: the guides tabulate them with the
# answers, and the Stata files store them as ordinary numbers. So a label rule is the only
# source (STANDARD.md §4.2, option 3). Whole labels only: "Never Heard / Not Sure" stays valid.
MISSING_LABELS = [
    (re.compile(r"^skipped$", re.I), "skipped"),
    (re.compile(r"^not asked$", re.I), "not_asked"),
    (re.compile(r"^(not sure|don'?t know)$", re.I), "dk"),
    (re.compile(r"^prefer not to say$", re.I), "refused"),
]


def load_slice():
    return yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())


def names_for(spec, release):
    if release == "cumulative":
        return list(spec["cumulative"])
    year = int(release)
    out = []
    for v in spec["cumulative"].values():
        n = (v.get("from") or {}).get(year)
        if n and n not in out:
            out.append(n)
    return out


def read_stata(release, names):
    path = RAW / RELEASES[release]["dta"]
    frame, meta = pyreadstat.read_dta(str(path), usecols=names, user_missing=True,
                                      apply_value_formats=False)
    return frame, meta


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def as_code(x):
    return int(x) if float(x).is_integer() else x


# ---------------------------------------------------------------- one record

def build_variable(release, name, spec_entry, guide, frame, meta):
    conf = RELEASES[release]
    data_src, guide_src = conf["data"], conf["guide"]
    report = []
    col = frame[name]
    record = {"dataset": DATASET, "release": release, "table": conf["table"], "name": name}
    fields = {}
    label = meta.column_names_to_labels.get(name)
    if label:
        record["label"] = label
        fields["label"] = {"origin": "data_file", "source": data_src}
    if guide.get("description"):
        record["description"] = guide["description"]
        fields["description"] = {"origin": "publisher", "source": guide_src, "location": guide["location"]}
    if guide.get("question"):
        record["questions"] = [{"text": guide["question"]}]
        fields["questions"] = {"origin": "rule", "rule": "ces_guide_text", "rule_version": RULE_VERSION,
                               "source": guide_src, "location": guide["location"],
                               "note": "The guide's text with layout line breaks removed"
                                       + ("; a grid item is its stem, ' -- ' and the item's label."
                                          if guide.get("item") else ".")}
    if guide.get("notes"):
        record["notes"] = "\n\n".join(guide["notes"])
        fields["notes"] = {"origin": "rule", "rule": "ces_guide_text", "rule_version": RULE_VERSION,
                           "source": guide_src, "location": guide["location"],
                           "note": "The guide's bullets other than Years, one paragraph each, with layout line breaks removed."}

    numbers = [v for v in col if is_number(v)]
    strings = [v for v in col if isinstance(v, str)]
    n_null = int(col.isna().sum())
    if numbers and not strings:
        record["storage_type"] = "integer" if all(float(v).is_integer() for v in numbers) else "float"
    elif strings:
        record["storage_type"] = "string"
    if "storage_type" in record:
        fields["storage_type"] = {"origin": "rule", "rule": "storage_type_from_values", "rule_version": RULE_VERSION,
                                  "source": data_src, "note": "integer if every value is whole, else float; "
                                                              "string for a text column."}
    if spec_entry.get("measure"):
        record["measure"] = spec_entry["measure"]
        fields["measure"] = {"origin": "hand", "author": AUTHOR, "date": DATE}

    labels_name = meta.variable_to_label.get(name)
    value_labels = meta.value_labels.get(labels_name, {}) if labels_name else {}
    counts = collections.Counter(as_code(v) for v in numbers)
    values = []
    for code, lab in value_labels.items():
        code = as_code(code)
        entry = {"code": code, "label": lab, "kind": "valid"}
        types = [t for pat, t in MISSING_LABELS if pat.search(lab.strip())]
        if types:
            entry.update(kind="missing", missing_types=types,
                         origin={"origin": "rule", "rule": "ces_missing_from_label", "rule_version": RULE_VERSION,
                                 "source": data_src, "note": "The whole label says why there is no substantive answer; the publisher doesn't mark the code missing."})
        values.append(entry)
    labelled = {v["code"] for v in values}
    if value_labels:
        for code, n in sorted(counts.items()):
            if code not in labelled:
                values.append({"code": code, "label": "", "kind": "unresolved",
                               "origin": {"origin": "data_file", "source": data_src,
                                          "note": f"Occurs {n} times in the data; the file has no label for it."}})
                report.append(f"`{release}` `{name}` code {code}: occurs {n} times, no value label; unresolved")
    if n_null and (values or record.get("measure") not in {"identifier", "weight"}):
        if release == "cumulative":
            values.append({"code": ".", "label": "(Missing)", "kind": "missing", "missing_types": ["unspecified"],
                           "origin": {"origin": "rule", "rule": "ces_system_missing", "rule_version": RULE_VERSION,
                                      "source": guide_src,
                                      "note": "Stata system missing. The guide's tabulations print it as "
                                              "\"(Missing)\" and give no reason; its introduction says, for "
                                              "example, that 2016's \"Not Asked\" and \"Skipped\" are both coded "
                                              "as NA, and the build code sets recoded-out categories to NA."}})
        else:
            values.append({"code": ".", "label": "", "kind": "missing", "missing_types": ["skipped", "not_asked"],
                           "origin": {"origin": "publisher", "source": guide_src,
                                      "location": "Part III, Common Content, introduction",
                                      "note": "Stata system missing. The guide: \"Observations with missing values "
                                              "(not shown in the counts) are either due to respondent skipping and "
                                              "missing due to the question not being asked to the respondent (e.g. "
                                              "due to branching).\""}})
    if values:
        record["values"] = values
        fields.setdefault("values", {"origin": "data_file", "source": data_src,
                                     "note": "Codes and labels from the Stata value labels, and codes that occur in the data."})
    elif numbers and record.get("measure") in {"continuous", "weight", "identifier"}:
        record["valid_range"] = {"min": as_code(min(numbers)), "max": as_code(max(numbers))}
        fields["valid_range"] = {"origin": "data_file", "source": data_src, "note": "Min and max of the values that occur."}

    if release == "cumulative":
        present = sorted({int(y) for y, v in zip(frame["year"], col) if v is not None and v == v})
    else:
        present = [int(release)] if len(col) > n_null else []
    if present:
        record["availability"] = [{"wave": y} for y in present]
        fields["availability"] = {"origin": "data_file", "source": data_src,
                                  "note": "Years with at least one non-missing value."}
        if release == "cumulative" and guide.get("years"):
            claimed = parse_years(guide["years"])
            if claimed is not None and set(claimed) != set(present):
                report.append(f"`cumulative` `{name}`: the guide's Years ({guide['years']}) differ from the "
                              f"years with data: only the guide {sorted(set(claimed) - set(present))}, "
                              f"only the data {sorted(set(present) - set(claimed))}")
    source = guide_src if guide else data_src
    record["source_location"] = {"source": source, **({"page": guide["page"]} if guide.get("page") else {})}
    record["provenance"] = {"record": {"origin": "publisher" if guide else "data_file", "source": source},
                            "fields": fields}
    return record, report


def parse_years(text):
    m = re.match(r"All of (\d{4})-(\d{4})(?: except (.*))?$", text)
    if m:
        out = set(range(int(m.group(1)), int(m.group(2)) + 1))
        if m.group(3):
            out -= {int(y) for y in re.findall(r"\d{4}", m.group(3))}
        return sorted(out)
    ys = re.findall(r"\b(?:19|20)\d{2}\b", text)
    return sorted({int(y) for y in ys}) if ys else None


# ---------------------------------------------------------------- links

def build_links(spec, built):
    """derived_from links from each cumulative variable to its yearly sources, per the build code."""
    links, report = [], []
    for name, entry in spec["cumulative"].items():
        for year, source_name in (entry.get("from") or {}).items():
            release = str(year)
            if (release, source_name) not in built or ("cumulative", name) not in built:
                report.append(f"link `{name}` <- {year} `{source_name}`: an end has no record; not written")
                continue
            changes = (entry.get("changes_by_year") or {}).get(year, entry.get("changes", []))
            relations = ["derived_from"] + list(changes)
            if name != source_name and "renamed" not in relations:
                relations.append("renamed")
            code_file, quote = entry["where"].split(":", 1)
            code_file = code_file.split()[0]
            links.append({"from": {"release": "cumulative", "table": "cumulative", "name": name},
                          "to": {"release": release, "table": "common", "name": source_name},
                          "relations": relations,
                          "waves": [year],
                          "origin": {"origin": "publisher", "source": BUILD_CODE[code_file],
                                     "location": entry["where"].split(":", 1)[0],
                                     "note": f"The build code: {quote.strip()}"}})
    return links, report


def yearly_links(records):
    """Rule ces_same_name_same_documentation: between consecutive yearly releases, the same name
    with the same label, question text, and codes and labels is the same item. The CES keeps
    a name for a continuing item (pid7, educ) and puts the year in the names of its yearly items
    (CC20_302, CC22_302), so a shared name is the publisher's convention for continuity. Pairs
    whose documentation differs are reported, not linked."""
    def doc(r):
        return (" ".join((r.get("label") or "").casefold().split()),
                [q.get("text") for q in r.get("questions", [])],
                [(v["code"], v["label"], v["kind"]) for v in r.get("values", []) if v["code"] != "."])
    links, report = [], []
    years = [r for r in RELEASES if r != "cumulative"]
    for a, b in zip(years, years[1:]):
        ra = {r["name"]: r for r in records.get(a, [])}
        rb = {r["name"]: r for r in records.get(b, [])}
        for name in sorted(set(ra) & set(rb)):
            if doc(ra[name]) == doc(rb[name]):
                links.append({"from": {"release": a, "table": "common", "name": name},
                              "to": {"release": b, "table": "common", "name": name},
                              "relations": ["same_item"],
                              "origin": {"origin": "rule", "rule": "ces_same_name_same_documentation",
                                         "rule_version": RULE_VERSION, "source": RELEASES[b]["guide"],
                                         "note": "Same name, label (ignoring case and spacing), question text, and codes and labels in both releases."}})
            else:
                report.append(f"`{name}` {a} -> {b}: same name, different documentation; not linked "
                              "(see pdt diff)")
    return links, report


def crosswalk_links(spec, built, linked, records):
    """Rule ces_same_item_from_crosswalk (STANDARD.md §5.2): when the build code maps the items of
    two consecutive years, unchanged, to the same cumulative variable, they are the same item.
    Unchanged: the link to the cumulative variable carries no relation but derived_from and
    renamed. Pairs another rule already linked are left as they are. When the two question
    texts differ (the approval grid names the incumbent), the link adds wording_changed; a
    guide that gives no text for one of them says nothing either way."""
    by = {(rel, r["name"]): r for rel, recs in records.items() for r in recs}

    def wording(rel, name):
        return [q.get("text") for q in by.get((rel, name), {}).get("questions", [])]
    links = []
    years = [int(r) for r in RELEASES if r != "cumulative"]
    for name, entry in spec["cumulative"].items():
        src = entry.get("from") or {}
        def unchanged(y):
            changes = (entry.get("changes_by_year") or {}).get(y, entry.get("changes", []))
            return set(changes) <= {"renamed"}
        for a, b in zip(years, years[1:]):
            if a not in src or b not in src or not (unchanged(a) and unchanged(b)):
                continue
            pair = ((str(a), src[a]), (str(b), src[b]))
            if pair in linked or pair[0] not in built or pair[1] not in built:
                continue
            linked.add(pair)
            code_file, quote = entry["where"].split(":", 1)
            links.append({"from": {"release": str(a), "table": "common", "name": src[a]},
                          "to": {"release": str(b), "table": "common", "name": src[b]},
                          "relations": ["same_item"] + (["renamed"] if src[a] != src[b] else [])
                                       + (["wording_changed"] if wording(str(a), src[a]) and wording(str(b), src[b])
                                          and wording(str(a), src[a]) != wording(str(b), src[b]) else []),
                          "origin": {"origin": "rule", "rule": "ces_same_item_from_crosswalk",
                                     "rule_version": RULE_VERSION, "source": BUILD_CODE[code_file.split()[0]],
                                     "location": code_file,
                                     "note": f"Both map, unchanged, to the cumulative file's {name}. "
                                             f"The build code: {quote.strip()}"}})
    return links


def compare_codes(spec, frames):
    """Where the cumulative file's codes for a year differ from the yearly file's (a check only)."""
    out = []
    cum = frames.get("cumulative")
    if cum is None:
        return out
    for name, entry in spec["cumulative"].items():
        for year, src in (entry.get("from") or {}).items():
            yf = frames.get(str(year))
            if yf is None or src not in yf or name not in cum:
                continue
            if entry.get("measure") in {"weight", "continuous", "identifier"}:
                continue
            a = collections.Counter(as_code(v) for v in cum.loc[cum["year"] == year, name] if is_number(v))
            b = collections.Counter(as_code(v) for v in yf[src] if is_number(v))
            if a != b:
                diff = sorted(c for c in set(a) & set(b) if a[c] != b[c])
                out.append(f"`{name}` vs {year} `{src}`: codes only in the cumulative {sorted(set(a) - set(b))}, "
                           f"only in the yearly file {sorted(set(b) - set(a))}, counts differ for {diff[:8]}")
    return out


# ---------------------------------------------------------------- main

def main(releases=None):
    spec = load_slice()
    releases = releases or list(RELEASES)
    built, frames, report, all_records = set(), {}, [], {}
    for release in releases:
        names = names_for(spec, release)
        conf = RELEASES[release]
        text = (RAW / conf["guide_txt"]).read_text()
        guide = (cumulative_entries(text, set(names)) if release == "cumulative"
                 else yearly_entries(text, names))
        read = names + (["year"] if release == "cumulative" and "year" not in names else [])
        frame, meta = read_stata(release, read)
        absent = [n for n in names if n not in frame.columns]
        if absent:
            report.append(f"`{release}`: not in the data file: {absent}")
        records = []
        for name in names:
            if name in absent:
                continue
            if release == "cumulative":
                entry = spec["cumulative"][name]
            else:
                entry = next((v for v in spec["cumulative"].values()
                              if (v.get("from") or {}).get(int(release)) == name), {})
            rec, rep = build_variable(release, name, entry, guide.get(name, {}), frame, meta)
            if not guide.get(name):
                rep.append(f"`{release}` `{name}`: not in the guide; record from the data file only")
            records.append(rec)
            report += rep
            built.add((release, name))
        all_records[release] = records
        write_jsonl(META / "releases" / release / conf["table"] / "variables.jsonl", records)
        frames[release] = frame
        print(f"{release}: {len(records)} records", file=sys.stderr)
    if set(releases) == set(RELEASES):
        links, rep = build_links(spec, built)
        report += rep
        more, rep = yearly_links(all_records)
        links += more
        report += rep
        linked = {((l["from"]["release"], l["from"]["name"]), (l["to"]["release"], l["to"]["name"]))
                  for l in more}
        links += crosswalk_links(spec, built, linked, all_records)
        write_jsonl(META / "links.jsonl", links)
        checks = compare_codes(spec, frames)
        lines = ["# Build report: ces", "", "Generated by `build/build_records.py`. Do not edit.", "",
                 f"- Records: {len(built)}", f"- Links: {len(links)}", "",
                 "## To look at", ""] + [f"- {r}" for r in report] + \
                ["", "## Cumulative codes vs. the yearly files (a check; the links come from the build code)", ""] + \
                [f"- {c}" for c in checks or ["No differences."]]
        (ROOT / "build" / "report.md").write_text("\n".join(lines) + "\n")
    else:
        print("\n".join(report), file=sys.stderr)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
