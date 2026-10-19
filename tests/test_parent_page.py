import pytest

import parent_page as pp

SUMMARY = "Builds and sends the monthly customer reports, used by operations staff."


def section_with(*entries: str) -> str:
    return pp.build_section(list(entries))


def test_first_write_on_empty_parent():
    """An empty parent gets the section with this service as its only entry."""
    body = pp.upsert_service_entry("", "reports", "Reports", SUMMARY)
    assert body == section_with(pp.build_entry("reports", "Reports", SUMMARY))


def test_first_write_keeps_hand_written_content():
    """A parent with no section keeps its text and gains the section at the end."""
    original = "<h1>Portal</h1><p>Written by people.</p>"
    body = pp.upsert_service_entry(original, "reports", "Reports", SUMMARY)
    assert body.startswith(original)
    assert body.endswith(section_with(pp.build_entry("reports", "Reports", SUMMARY)))


def test_new_service_inserted_alphabetically_between_others():
    """A service joining an existing section lands in name order; the others are untouched."""
    alpha = pp.build_entry("alpha", "Alpha", "Alpha does something for somebody.")
    zulu = pp.build_entry("zulu", "Zulu", "Zulu does something else for others.")
    original = "<p>Intro</p>" + section_with(alpha, zulu) + "<p>Footer</p>"
    body = pp.upsert_service_entry(original, "reports", "Reports", SUMMARY)
    expected_entry = pp.build_entry("reports", "Reports", SUMMARY)
    assert (
        body
        == "<p>Intro</p>" + section_with(alpha, expected_entry, zulu) + "<p>Footer</p>"
    )


def test_new_service_appended_last():
    """A service that sorts after every existing entry goes last, before the end marker."""
    alpha = pp.build_entry("alpha", "Alpha", "Alpha does something for somebody.")
    original = section_with(alpha)
    body = pp.upsert_service_entry(original, "reports", "Reports", SUMMARY)
    assert body == section_with(alpha, pp.build_entry("reports", "Reports", SUMMARY))


def test_existing_entry_replaced_and_nothing_else_moves():
    """Only this service's paragraph changes; neighbours and outside text stay byte-identical."""
    alpha = pp.build_entry("alpha", "Alpha", "Alpha does something for somebody.")
    old = pp.build_entry(
        "reports", "Reports", "An older description of the reports service."
    )
    original = "<p>Intro</p>" + section_with(alpha, old) + "<p>Footer</p>"
    body = pp.upsert_service_entry(original, "reports", "Reports", SUMMARY)
    new = pp.build_entry("reports", "Reports", SUMMARY)
    assert body == "<p>Intro</p>" + section_with(alpha, new) + "<p>Footer</p>"


def test_anchor_with_editor_attributes_is_recognised():
    """Confluence adds attributes to macros on save; the markers must still be found."""
    edited = pp.build_section(
        [pp.build_entry("reports", "Reports", "An older description of reports.")]
    )
    edited = edited.replace(
        '<ac:structured-macro ac:name="anchor">',
        '<ac:structured-macro ac:name="anchor" ac:schema-version="1" ac:macro-id="abc-123">',
    )
    body = pp.upsert_service_entry(edited, "reports", "Reports", SUMMARY)
    assert body.count(pp.ENTRY_PREFIX + "reports") == 1
    assert pp.existing_entry_text(body, "reports") == SUMMARY


def test_existing_entry_text():
    """The current summary is read back as plain text; a missing entry reads as None."""
    body = section_with(pp.build_entry("reports", "Reports", "Sends reports & more."))
    assert pp.existing_entry_text(body, "reports") == "Sends reports & more."
    assert pp.existing_entry_text(body, "prism") is None
    assert pp.existing_entry_text("<p>No section</p>", "reports") is None


def test_summary_is_escaped():
    """Text from the model can never become markup on the parent page."""
    body = pp.upsert_service_entry(
        "", "reports", "Reports", "Uses A & B when C > D for the whole fleet."
    )
    assert "&amp;" in body


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "Too short",
        "x" * (pp.MAX_SUMMARY_CHARS + 1),
        "Has <p>markup</p> inside the summary text.",
        "Has **bold** markdown inside the summary.",
        "# A heading rather than a summary sentence",
    ],
)
def test_bad_summaries_are_refused(bad):
    """Empty, oversized or marked-up summaries fail rather than reach the page."""
    with pytest.raises(ValueError):
        pp.upsert_service_entry("", "reports", "Reports", bad)


def test_start_marker_without_end_fails():
    """A damaged section stops the run instead of guessing its extent."""
    damaged = (
        f"<p>{pp.anchor_macro(pp.SECTION_START)}</p><h2>Services in this area</h2>"
    )
    with pytest.raises(ValueError):
        pp.upsert_service_entry(damaged, "reports", "Reports", SUMMARY)


def test_whitespace_in_summary_collapsed():
    """A multi-line model answer becomes one paragraph."""
    body = pp.upsert_service_entry(
        "", "reports", "Reports", "Builds the monthly\n\nreports for   staff."
    )
    assert (
        pp.existing_entry_text(body, "reports")
        == "Builds the monthly reports for staff."
    )
