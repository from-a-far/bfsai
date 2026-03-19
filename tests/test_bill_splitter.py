from pathlib import Path

from PIL import Image

from app.bill_splitter import (
    complete_batch_session,
    create_batch_session,
    load_batch_session,
    remove_pages_from_batch,
    save_selected_pages_as_bill,
)
from app.viewer import describe_document_pages


def make_batch_pdf(path: Path, page_count: int) -> None:
    pages = [Image.new("RGB", (220, 320), color=(index * 30 % 255, 120, 180)) for index in range(page_count)]
    try:
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    finally:
        for page in pages:
            page.close()


def test_create_batch_session_tracks_all_pages(tmp_path: Path) -> None:
    source_path = tmp_path / "batch.pdf"
    make_batch_pdf(source_path, 4)

    session = create_batch_session(tmp_path, source_path, original_filename="batch.pdf")

    assert session.source_path == source_path.resolve()
    assert session.output_dir == source_path.resolve().with_name("batch_indbills")
    assert session.remaining_pages == [1, 2, 3, 4]
    assert session.output_dir.exists()


def test_save_selected_pages_as_bill_writes_pdf_in_output_folder(tmp_path: Path) -> None:
    source_path = tmp_path / "batch.pdf"
    make_batch_pdf(source_path, 5)
    session = create_batch_session(tmp_path, source_path, original_filename="batch.pdf")

    output = save_selected_pages_as_bill(tmp_path, session.batch_id, "2,4,5")
    reloaded = load_batch_session(tmp_path, session.batch_id)

    stem, suffix = output.name.split("_", 1)
    assert len(stem) == 8
    assert stem.isalnum()
    assert suffix == "indbill.pdf"
    assert output.path.exists()
    assert output.page_numbers == [2, 4, 5]
    assert len(describe_document_pages(output.path)) == 3
    assert len(reloaded.saved_outputs) == 1
    assert reloaded.remaining_pages == [1, 3]


def test_remove_pages_from_batch_updates_remaining_pages(tmp_path: Path) -> None:
    source_path = tmp_path / "batch.pdf"
    make_batch_pdf(source_path, 5)
    session = create_batch_session(tmp_path, source_path, original_filename="batch.pdf")

    updated = remove_pages_from_batch(tmp_path, session.batch_id, [2, 4])

    assert updated.remaining_pages == [1, 3, 5]
    reloaded = load_batch_session(tmp_path, session.batch_id)
    assert reloaded.remaining_pages == [1, 3, 5]


def test_complete_batch_session_renames_source_and_opens_next_file(tmp_path: Path) -> None:
    source_a = tmp_path / "a_batch.pdf"
    source_b = tmp_path / "b_batch.pdf"
    make_batch_pdf(source_a, 2)
    make_batch_pdf(source_b, 3)
    session = create_batch_session(tmp_path, source_a, original_filename=source_a.name)
    remove_pages_from_batch(tmp_path, session.batch_id, [1, 2])

    transition = complete_batch_session(tmp_path, session.batch_id)

    assert transition.completed_source_path.name == "a_batch_d.pdf"
    assert transition.completed_source_path.exists()
    assert not source_a.exists()
    assert transition.next_batch is not None
    assert transition.next_batch.source_path == source_b.resolve()


def test_complete_batch_session_without_next_file_returns_none(tmp_path: Path) -> None:
    source_path = tmp_path / "batch.pdf"
    make_batch_pdf(source_path, 1)
    session = create_batch_session(tmp_path, source_path, original_filename=source_path.name)
    remove_pages_from_batch(tmp_path, session.batch_id, [1])

    transition = complete_batch_session(tmp_path, session.batch_id)

    assert transition.completed_source_path.name == "batch_d.pdf"
    assert transition.next_batch is None
