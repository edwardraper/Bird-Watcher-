"""What the builder is allowed to believe about a file on Commons.

Every case here is one that actually came back from Commons while the
database was being built, and got the wrong answer until the rule under
test existed.
"""

from __future__ import annotations

from scripts.build_plate_db import Candidate, SpeciesEntry, score_candidate

ROBIN = SpeciesEntry(
    rank=3,
    common_name="Robin",
    scientific_name="Erithacus rubecula",
    synonyms=("Sylvia rubecula",),
)
BLACKBIRD = SpeciesEntry(
    rank=1, common_name="Blackbird", scientific_name="Turdus merula"
)
GOSHAWK = SpeciesEntry(
    rank=99,
    common_name="Goshawk",
    scientific_name="Astur gentilis",
    synonyms=("Accipiter gentilis",),
)

LILFORD = "Coloured figures of the birds of the British Islands - issued by Lord Lilford"


def candidate(**overrides) -> Candidate:
    base = dict(
        title="File:Erithacus rubecula plate.jpg",
        filename="Erithacus rubecula plate.jpg",
        mime="image/jpeg",
        width=1200,
        height=1800,
        description="ROBIN. Erithacus rubecula (Linn.). J.G.Keulemans lith.",
        artist="Keulemans, J. G.",
        categories=("Erithacus rubecula (illustrations)",),
    )
    base.update(overrides)
    return Candidate(**base)


def score(entry: SpeciesEntry, from_category: bool = False, **overrides) -> int:
    return score_candidate(entry, candidate(**overrides), from_category).score


# -- the artist gate -------------------------------------------------------


def test_a_plate_by_someone_else_is_refused() -> None:
    assert score(ROBIN, artist="Archibald Thorburn", description="ROBIN.") <= 0


def test_a_thorburn_signature_beats_a_shared_author_list() -> None:
    """Lilford's plates credit every artist in the book. The signature on
    the plate is about the plate."""
    assert (
        score(
            ROBIN,
            artist="Keulemans, J. G.; Lilford; Thorburn, Archibald",
            description="ROBIN. Erithacus rubecula. A. Thorburn del.",
        )
        <= 0
    )


def test_ocr_damage_to_the_signature_is_forgiven() -> None:
    """Scans read "J.G.KeuleTnans libti." often enough to matter."""
    assert score(ROBIN, artist="", description="ROBIN. J.G.KeuleTnans libti.") > 0


# -- the species gate ------------------------------------------------------


def test_a_different_species_in_the_genus_is_refused() -> None:
    """Commons files the Persian robin under Erithacus rubecula."""
    assert (
        score(
            ROBIN,
            title="File:A history of the birds of Europe (Pl. 644).jpg",
            filename="A history of the birds of Europe (Pl. 644).jpg",
            description="J.G.Keulemans lith. PERSIAN ROBIN. ERITHACUS HYRCANUS.",
        )
        <= 0
    )


def test_a_run_together_filename_naming_another_bird_is_refused() -> None:
    """MerulaBourdilloniKeulemans.jpg is a Nilgiri blackbird, however it
    happens to be categorised."""
    assert (
        score(
            BLACKBIRD,
            title="File:MerulaBourdilloniKeulemans.jpg",
            filename="MerulaBourdilloniKeulemans.jpg",
            description="Keulemans",
            categories=("Turdus merula simillimus",),
        )
        <= 0
    )


def test_the_common_name_alone_is_not_identification() -> None:
    """A Nilgiri blackbird is also captioned "blackbird"."""
    assert (
        score(
            BLACKBIRD,
            title="File:A blackbird.jpg",
            filename="A blackbird.jpg",
            description="A blackbird, drawn by Keulemans.",
            categories=(),
        )
        <= 0
    )


def test_the_illustrations_category_identifies_a_plate_with_unreadable_ocr() -> None:
    """Lilford scans often OCR to noise; a human filed them by species."""
    assert (
        score(
            ROBIN,
            from_category=True,
            title=f"File:{LILFORD} (10036178774).jpg",
            filename=f"{LILFORD} (10036178774).jpg",
            description="ii 4 i \\ a; o Q i Q o E O 8 o o",
            artist="Keulemans, J. G.; Lilford; Thorburn, Archibald",
            categories=("Files from the Biodiversity Heritage Library",),
        )
        > 0
    )


def test_an_old_genus_still_identifies_the_bird() -> None:
    """Victorian captions predate half of modern taxonomy."""
    assert (
        score(
            GOSHAWK,
            title="File:Accipiter gentilis plate.jpg",
            filename="Accipiter gentilis plate.jpg",
            description="GOSHAWK. Accipiter gentilis. Keulemans lith.",
            categories=(),
        )
        > 0
    )


# -- preference ------------------------------------------------------------


def test_a_volume_or_cover_is_never_a_plate() -> None:
    assert (
        score(
            ROBIN,
            title="File:Onze vogels in huis en tuin. J. G. Keulemans. Vol. I. 1869.jpg",
            filename="Onze vogels in huis en tuin. J. G. Keulemans. Vol. I. 1869.jpg",
        )
        <= 0
    )


def test_lilford_outranks_onze_vogels() -> None:
    """Both are Keulemans and both are the right bird. Lilford's plates sit
    on bare paper; the 1869 birds sit in a painted landscape."""
    lilford = score(
        ROBIN,
        title=f"File:{LILFORD} (10036178774).jpg",
        filename=f"{LILFORD} (10036178774).jpg",
        description="ROBIN. Erithacus rubecula (Linn.).",
        artist="Keulemans, J. G.; Lilford; Thorburn, Archibald",
    )
    onze = score(
        ROBIN,
        title="File:Erithacus rubecula 1869.jpg",
        filename="Erithacus rubecula 1869.jpg",
        description="Onze vogels in huis en tuin. Erithacus rubecula.",
    )
    assert lilford > onze > 0


def test_whole_volumes_and_thumbnails_are_refused() -> None:
    assert score(ROBIN, mime="application/pdf") <= 0
    assert score(ROBIN, width=300, height=400) <= 0
