from pathlib import Path

import pytest

from quality_report.config import DEFAULT_EXCLUDES
from quality_report.rusttests import (
    find_rust_test_code,
    is_test_attribute,
    mask,
    scan_source,
)

EXCLUDE = list(DEFAULT_EXCLUDES)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _ranges(src: str) -> list[tuple[int, int]]:
    return sorted(scan_source(src).ranges)


# --- mask ---


def test_mask_keeps_length_and_newlines():
    src = 'let a = "x\n{";\n// c {\n/* b\n} */ let b = 1;\n'
    masked = mask(src)
    assert len(masked) == len(src)
    assert [i for i, c in enumerate(masked) if c == "\n"] == [
        i for i, c in enumerate(src) if c == "\n"
    ]
    assert "{" not in masked and "}" not in masked
    assert "let b = 1;" in masked


def test_mask_nested_block_comments():
    assert mask("/* a /* { */ } */x") == " " * 17 + "x"


def test_mask_strings_with_escapes_and_raw_strings():
    src = r"""a("q\"{", r#"}"#, br"{", b"}", c"{") z"""
    masked = mask(src)
    assert not set("{}") & set(masked)
    assert masked.startswith("a(") and masked.endswith(") z")


def test_mask_char_literals_but_not_lifetimes():
    src = "f('{', '\\'', '\\u{7d}', b'}'); fn g<'a>(x: &'a str) {}"
    masked = mask(src)
    assert masked.count("{") == 1 and masked.count("}") == 1
    assert "<'a>" in masked and "&'a str" in masked


def test_mask_raw_identifier_is_not_a_raw_string():
    assert mask('let r#type = "x";') == "let r#type =    ;"


# --- attributes ---


@pytest.mark.parametrize(
    "content",
    [
        "test",
        "tokio::test",
        'tokio::test(flavor = "multi_thread")',
        "cfg(test)",
        "cfg( test )",
        'cfg(all(test, target_os = "linux"))',
        "cfg(any(test, all(test, unix)))",
    ],
)
def test_test_attributes(content):
    assert is_test_attribute(content)


@pytest.mark.parametrize(
    "content",
    [
        "allow(dead_code)",
        "cfg(not(test))",
        'cfg(any(test, feature = "x"))',
        "cfg(unix)",
        "cfg_attr(test, allow(dead_code))",
        "derive(Debug)",
        "testing",
        "cfg(all())",
    ],
)
def test_non_test_attributes(content):
    assert not is_test_attribute(content)


# --- scan_source ---


def test_inline_test_module_range():
    src = "fn a() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}\nfn b() {}\n"
    assert _ranges(src) == [(3, 7), (5, 6)]


def test_nested_test_module_inside_production_module():
    src = (
        "mod outer {\n"  # 1
        "    pub fn p() {}\n"
        "    #[cfg(test)]\n"  # 3
        "    mod tests {\n"
        "        fn h() {}\n"
        "    }\n"  # 6
        "    pub fn q() {}\n"
        "}\n"
    )
    assert _ranges(src) == [(3, 6)]


def test_attribute_stack_starts_at_first_attribute():
    src = "#[allow(unused)]\n#[cfg(test)]\nfn helper() {\n}\n#[cfg(test)]\n#[allow(x)]\nfn h2() {}\n"
    assert _ranges(src) == [(1, 4), (5, 7)]


def test_test_function_outside_a_module():
    src = (
        "fn p() {}\n#[tokio::test(start_paused = true)]\nasync fn t() {\n    p();\n}\n"
    )
    assert _ranges(src) == [(2, 5)]


def test_statement_level_cfg_test():
    src = (
        "fn f(x: &mut u8) {\n"
        "    #[cfg(test)]\n"  # 2
        "    if HOOK.get() {\n"
        "        *x = 1;\n"
        "    }\n"  # 5
        "    *x += 1;\n"
        "}\n"
    )
    assert _ranges(src) == [(2, 5)]


def test_item_ending_in_semicolon_and_brackets():
    src = (
        "#[cfg(test)]\n"
        "const X: [u8; 2] = [\n"
        "    1, 2,\n"
        "];\n"  # 4
        "#[cfg(test)]\n"
        "use a::{\n"
        "    b, c,\n"
        "};\n"  # 8
        "#[cfg(test)]\n"
        "static S: Lazy<T> = Lazy::new(|| {\n"
        "    T::new()\n"
        "});\n"  # 12
        "fn p() {}\n"
    )
    assert _ranges(src) == [(1, 4), (5, 8), (9, 12)]


def test_struct_field_and_last_field_without_comma():
    src = "struct S {\n    a: u8,\n    #[cfg(test)]\n    b: u8,\n    #[cfg(test)]\n    c: u8\n}\n"
    assert _ranges(src) == [(3, 4), (5, 6)]


def test_macro_item_and_braces_in_literals_inside_test_code():
    src = (
        "#[cfg(test)]\n"
        "thread_local! {\n"
        '    static A: &str = "}}}";\n'
        "}\n"  # 4
        "#[cfg(test)]\n"
        "mod t {\n"
        '    const R: &str = r#"\n'
        "}\n"
        '"#;\n'
        "    const C: char = '}';\n"
        "}\n"  # 11
        "fn p() {}\n"
    )
    assert _ranges(src) == [(1, 4), (5, 11)]


def test_attributes_in_comments_and_strings_do_not_count():
    src = '/// #[cfg(test)]\nfn a() {}\n// #[test]\nfn b() { let s = "#[test]"; }\n'
    assert _ranges(src) == []


def test_inner_cfg_test_at_top_marks_whole_file():
    scan = scan_source("//! Test helpers.\n#![cfg(test)]\n\npub fn h() {}\n")
    assert scan.whole_file
    assert scan.ranges == []


def test_inner_cfg_test_inside_module_marks_its_block():
    src = "mod m {\n    #![cfg(test)]\n    fn h() {}\n}\nfn p() {}\n"
    scan = scan_source(src)
    assert not scan.whole_file
    assert scan.ranges == [(1, 4)]


def test_other_inner_attributes_change_nothing():
    assert not scan_source("#![allow(dead_code)]\nfn p() {}\n").whole_file


def test_out_of_line_module_declarations():
    src = (
        "mod prod;\n"
        "#[cfg(test)]\n"
        "mod testutil;\n"
        '#[path = "x/y.rs"]\n'
        "mod renamed;\n"
        "mod inline {\n"
        "    pub mod child;\n"
        "}\n"
    )
    decls = {d.name: d for d in scan_source(src).mod_decls}
    assert set(decls) == {"prod", "testutil", "renamed", "child"}
    assert decls["testutil"].line == 3
    assert decls["renamed"].path_attr == "x/y.rs"
    assert decls["child"].inline_parents == ["inline"]
    assert decls["prod"].inline_parents == []


# --- find_rust_test_code ---


def test_find_rust_test_code_files_and_ranges(tmp_path):
    _write(
        tmp_path,
        {
            "Cargo.toml": "[package]\n",
            "src/lib.rs": (
                "mod prod;\n"
                "#[cfg(test)]\n"
                "mod testutil;\n"
                "#[cfg(test)]\n"
                '#[path = "helpers/h.rs"]\n'
                "mod h;\n"
                "mod a {\n"
                "    #[cfg(test)]\n"
                "    mod b;\n"
                "}\n"
                "#[cfg(test)]\n"
                "mod tests {\n"
                "    mod more;\n"
                "}\n"
            ),
            "src/prod.rs": "mod sub;\npub fn p() {}\n#[test]\nfn t() {}\n",
            "src/prod/sub.rs": "pub fn s() {}\n",
            "src/testutil.rs": "mod deep;\n",
            "src/testutil/deep/mod.rs": "pub fn d() {}\n",
            "src/helpers/h.rs": "pub fn h() {}\n",
            "src/a/b.rs": "pub fn b() {}\n",
            "src/tests/more.rs": "pub fn m() {}\n",
            "src/gated.rs": "#![cfg(test)]\nmod below;\n",
            "src/gated/below.rs": "pub fn x() {}\n",
            "tests/it.rs": "mod common;\n",
            "tests/common/mod.rs": "pub fn c() {}\n",
            "target/debug/build/x.rs": "#[cfg(test)] mod y;\n",
        },
    )
    found = find_rust_test_code(tmp_path, EXCLUDE)
    assert found.files == {
        "src/testutil.rs",
        "src/testutil/deep/mod.rs",
        "src/helpers/h.rs",
        "src/a/b.rs",
        "src/tests/more.rs",
        "src/gated.rs",
        "src/gated/below.rs",
        "tests/it.rs",
        "tests/common/mod.rs",
    }
    assert found.ranges == {
        "src/lib.rs": [(2, 3), (4, 6), (8, 9), (11, 14)],
        "src/prod.rs": [(3, 4)],
    }
    assert found.is_test_line("src/prod.rs", 4)
    assert not found.is_test_line("src/prod.rs", 2)
    assert found.is_test_line("tests/it.rs", 1)
    assert not found.is_test_line("src/prod/sub.rs", 1)


def test_tests_dir_counts_only_at_a_crate_root(tmp_path):
    _write(
        tmp_path,
        {
            "Cargo.toml": "[workspace]\n",
            "crates/core/Cargo.toml": "[package]\n",
            "crates/core/tests/roundtrip.rs": "#[test]\nfn t() {}\n",
            "crates/core/src/lib.rs": "pub mod tests;\n",
            "crates/core/src/tests.rs": "pub fn not_a_test() {}\n",
        },
    )
    found = find_rust_test_code(tmp_path, EXCLUDE)
    assert found.files == {"crates/core/tests/roundtrip.rs"}


def test_bin_crate_root_resolves_modules_beside_it(tmp_path):
    _write(
        tmp_path,
        {
            "Cargo.toml": "[package]\n",
            "src/bin/tool.rs": "#[cfg(test)]\nmod t;\nfn main() {}\n",
            "src/bin/t.rs": "fn x() {}\n",
        },
    )
    assert find_rust_test_code(tmp_path, EXCLUDE).files == {"src/bin/t.rs"}


def test_excluded_rust_files_are_not_scanned(tmp_path):
    _write(tmp_path, {"vendor/x/src/lib.rs": "#[cfg(test)]\nmod t {}\n"})
    found = find_rust_test_code(tmp_path, EXCLUDE)
    assert found.files == set() and found.ranges == {}


def test_unreadable_rust_file_warns(tmp_path, capsys):
    _write(tmp_path, {"src/lib.rs": "fn p() {}\n"})
    (tmp_path / "src" / "lib.rs").chmod(0)
    try:
        find_rust_test_code(tmp_path, EXCLUDE)
    finally:
        (tmp_path / "src" / "lib.rs").chmod(0o644)
    assert "src/lib.rs is unreadable" in capsys.readouterr().err
