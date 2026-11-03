from mirror import profile


def test_writing_stats_counts_sentences_and_endings():
    st = profile.writing_stats(["오늘은 피곤했다. 그래서 쉬었어요.", "정리했습니다."])
    assert st["observations"] == 2
    assert st["sentences"] == 3
    names = {e["name"]: e["count"] for e in st["endings"]}
    assert names == {"Plain (-다)": 1, "Polite (-요)": 1, "Formal (-ㅂ니다)": 1}


def test_writing_stats_empty():
    assert profile.writing_stats([])["sentences"] == 0


def test_evidence_label():
    assert profile._evidence_label(1).startswith("Candidate")
    assert "3 evidence" in profile._evidence_label(3)
