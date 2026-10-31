from backend.matcher.taxonomy import TaxonomyV3, load_taxonomy


def test_taxonomy_v3_is_versioned_and_deterministic() -> None:
    taxonomy = load_taxonomy()
    assert isinstance(taxonomy, TaxonomyV3)
    assert taxonomy.version == "v3"
    assert taxonomy.lookup("ML") == taxonomy.lookup("machine learning")


def test_taxonomy_matches_longest_known_phrase() -> None:
    matches = load_taxonomy().match("Senior Machine Learning Engineer")
    assert matches[0].canonical == "machine learning"
    assert matches[0].family == "machine_learning"


def test_taxonomy_ignores_unknown_tokens() -> None:
    assert load_taxonomy().match("Chief Wizard of Whatever") == ()
