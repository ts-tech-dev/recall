from recall.chunker import MAX_WORDS, chunk_markdown, slugify, split_sections


def test_sections_follow_heading_hierarchy():
    md = "intro\n# A\ntext a\n## B\ntext b\n# C\ntext c"
    secs = split_sections(md, "Doc")
    assert [s[0] for s in secs] == [["Doc"], ["A"], ["A", "B"], ["C"]]


def test_heading_inside_code_fence_ignored():
    md = "# Real\n```\n# comment\n```\nafter"
    secs = split_sections(md, "Doc")
    assert len(secs) == 1 and "# comment" in secs[0][1]


def test_images_attached_to_their_chunk():
    md = "# One\ntext\n\n![diagram](/api/file?path=a.png)\n# Two\nno images"
    chunks = chunk_markdown(md, "Doc")
    assert chunks[0].images == [{"alt": "diagram", "url": "/api/file?path=a.png"}]
    assert chunks[1].images == []
    assert "[image: diagram]" in chunks[0].text  # alt text is searchable


def test_unresolved_image_paths_not_attached():
    chunks = chunk_markdown("# X\n![a](missing.png)", "Doc")
    assert chunks[0].images == []


def test_long_sections_split_with_limits():
    paras = "\n\n".join(" ".join(f"w{i}_{j}" for j in range(100)) for i in range(20))
    chunks = chunk_markdown("# Long\n" + paras, "Doc")
    assert len(chunks) > 3
    assert all(len(c.text.split()) <= MAX_WORDS + 100 for c in chunks)
    assert all(c.heading == "Long" for c in chunks)


def test_huge_table_split_repeats_header():
    rows = "\n".join(f"| host{i} | 10.0.0.{i} | some role words here |" for i in range(400))
    md = "# Hosts\n| Host | IP | Role |\n| --- | --- | --- |\n" + rows
    chunks = chunk_markdown(md, "Doc")
    assert len(chunks) > 2
    assert all(c.text.startswith("| Host | IP | Role |") for c in chunks)


def test_slugify_matches_previewer():
    assert slugify("Slide 1: Quarterly roadmap") == "slide-1-quarterly-roadmap"
    assert slugify("  Trunk  configuration ") == "trunk-configuration"


def test_custom_heading_id_and_image_size():
    md = "# Guide\n\n## Setup steps {#setup}\n\nRun it. ![Diagram|300](/api/file?path=d.png)\n"
    c = [x for x in chunk_markdown(md, "Guide") if x.text][0]
    assert c.anchor == "setup" and c.heading == "Guide > Setup steps"
    assert c.images == [{"alt": "Diagram", "url": "/api/file?path=d.png"}] and "[image: Diagram]" in c.text
