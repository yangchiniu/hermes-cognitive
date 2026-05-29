"""
Real Task Corpus

Defines a corpus of real-world tasks that Hermes could actually be asked to do.
Each task includes a natural language goal, expected high-level steps, difficulty
rating, and metadata tags.

Standard library only (dataclasses, random).
"""

import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Core data type
# ---------------------------------------------------------------------------

@dataclass
class RealTask:
    """A real-world task definition for benchmarking Hermes.

    Attributes:
        task_id: Unique identifier for this task.
        category: One of: web_collect, paper_search, news_collect, data_extract,
                  recovery, multi_step, file_ops.
        goal: The natural language goal as a user would state it.
        expected_steps: High-level steps Hermes would execute to complete this task.
        difficulty: easy | medium | hard.
        estimated_duration_s: Rough wall-clock estimate for a real run in seconds.
        tags: Metadata labels for filtering and categorization.
    """
    task_id: str
    category: str
    goal: str
    expected_steps: list[str]
    difficulty: str  # easy | medium | hard
    estimated_duration_s: int
    tags: list[str]

    def __post_init__(self):
        assert self.difficulty in ("easy", "medium", "hard"), \
            f"Invalid difficulty: {self.difficulty}"
        assert self.category in (
            "web_collect", "paper_search", "news_collect",
            "data_extract", "recovery", "multi_step", "file_ops"
        ), f"Invalid category: {self.category}"


# ---------------------------------------------------------------------------
# Category constants
# ---------------------------------------------------------------------------

CATEGORIES = [
    "web_collect",
    "paper_search",
    "news_collect",
    "data_extract",
    "recovery",
    "multi_step",
    "file_ops",
]

# ---------------------------------------------------------------------------
# Task corpus
# ---------------------------------------------------------------------------

TASKS = [
    # =========================================================================
    # 1. WEB COLLECTION (20 tasks)
    # =========================================================================
    RealTask(
        task_id="web-001",
        category="web_collect",
        goal="Search for average housing prices in Austin, TX over the past 6 months",
        expected_steps=[
            "Search for housing price reports in Austin, TX",
            "Collect pricing data from real estate aggregator sites",
            "Extract average, median, and trend figures",
            "Organize results by month or neighborhood",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["housing", "real-estate", "pricing", "location:Austin"],
    ),
    RealTask(
        task_id="web-002",
        category="web_collect",
        goal="Collect restaurant reviews for the top 5 Italian restaurants in Boston",
        expected_steps=[
            "Search for best Italian restaurants in Boston",
            "Open each restaurant's review page",
            "Extract rating, review count, and recent review snippets",
            "Compile into a structured comparison table",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["restaurants", "reviews", "food", "location:Boston"],
    ),
    RealTask(
        task_id="web-003",
        category="web_collect",
        goal="Gather product specifications for the latest flagship smartphones from Apple, Samsung, and Google",
        expected_steps=[
            "Search for latest phone models from each manufacturer",
            "Open official product pages or spec sheets",
            "Extract CPU, RAM, storage, camera, display, battery specs",
            "Normalize units and build a comparison table",
        ],
        difficulty="medium",
        estimated_duration_s=300,
        tags=["tech", "phones", "specifications", "comparison"],
    ),
    RealTask(
        task_id="web-004",
        category="web_collect",
        goal="Search for software engineering intern job postings at top tech companies for summer 2025",
        expected_steps=[
            "Search for software engineering internship postings",
            "Visit company career pages or job boards",
            "Extract role title, location, pay range, required skills, deadlines",
            "Deduplicate and organize by company",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["jobs", "internships", "software-engineering", "career"],
    ),
    RealTask(
        task_id="web-005",
        category="web_collect",
        goal="Collect 7-day weather forecast data for Tokyo, London, and Sydney",
        expected_steps=[
            "Search for weather forecasts for each city",
            "Extract high/low temps, precipitation %, humidity, wind speed per day",
            "Normalize to common units (Celsius, mm, km/h)",
            "Produce a day-by-day comparison table",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["weather", "forecast", "cities", "comparison"],
    ),
    RealTask(
        task_id="web-006",
        category="web_collect",
        goal="Search for a 10-day travel itinerary for a family of 4 visiting Paris, including attractions, hotels, and costs",
        expected_steps=[
            "Search for family-friendly travel itineraries for Paris",
            "Collect attraction recommendations, opening hours, ticket prices",
            "Find hotel options near central attractions with family room rates",
            "Estimate daily costs and compile a day-by-day plan",
        ],
        difficulty="medium",
        estimated_duration_s=360,
        tags=["travel", "itinerary", "Paris", "family", "budget"],
    ),
    RealTask(
        task_id="web-007",
        category="web_collect",
        goal="Find event schedules for tech conferences happening in Berlin during June 2025",
        expected_steps=[
            "Search for tech conferences in Berlin June 2025",
            "Collect dates, venues, speaker lineups, ticket prices",
            "Extract agenda highlights and registration deadlines",
            "Sort chronologically and summarize",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["events", "conferences", "tech", "Berlin", "calendar"],
    ),
    RealTask(
        task_id="web-008",
        category="web_collect",
        goal="Collect contact information (email, phone, address) for 10 top-rated dentists in Chicago",
        expected_steps=[
            "Search for top-rated dentists in Chicago",
            "Visit each practice's website or directory page",
            "Extract phone number, email, office address, hours",
            "Validate format and compile into a contact list",
        ],
        difficulty="easy",
        estimated_duration_s=150,
        tags=["contact-info", "dentists", "healthcare", "location:Chicago"],
    ),
    RealTask(
        task_id="web-009",
        category="web_collect",
        goal="Search for academic conference deadlines in machine learning for 2025",
        expected_steps=[
            "Search for ML conference deadlines (NeurIPS, ICML, ICLR, AISTATS, etc.)",
            "Collect submission deadlines, notification dates, conference dates",
            "Extract submission format requirements (page limit, anonymity)",
            "Organize as a calendar with deadlines sorted chronologically",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["academic", "conferences", "deadlines", "machine-learning"],
    ),
    RealTask(
        task_id="web-010",
        category="web_collect",
        goal="Find the latest government policy documents on AI regulation from the EU",
        expected_steps=[
            "Search for EU AI Act policy documents and official publications",
            "Navigate to official EU legislative portal",
            "Collect key dates, regulatory tiers, compliance requirements",
            "Summarize enforcement timeline and obligations",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["government", "policy", "AI-regulation", "EU", "legal"],
    ),
    RealTask(
        task_id="web-011",
        category="web_collect",
        goal="Collect quarterly financial data (revenue, net income, EPS) for Apple Inc. over the last 3 years",
        expected_steps=[
            "Search for Apple Inc. quarterly earnings reports",
            "Extract revenue, net income, EPS for each quarter",
            "Note YoY growth percentages",
            "Format as a time-series table",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["finance", "earnings", "Apple", "stock", "quarterly"],
    ),
    RealTask(
        task_id="web-012",
        category="web_collect",
        goal="Search for open datasets related to climate change suitable for data science projects",
        expected_steps=[
            "Search on Kaggle, Data.gov, and academic repositories for climate datasets",
            "Collect dataset description, size, format, license, and download URL",
            "Filter for datasets with CSVs or easy API access",
            "Compile a ranked list with suitability notes",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["datasets", "climate", "open-data", "data-science"],
    ),
    RealTask(
        task_id="web-013",
        category="web_collect",
        goal="Find the official API documentation for OpenAI, Anthropic, and Google Gemini APIs",
        expected_steps=[
            "Search for each provider's API documentation homepage",
            "Collect authentication methods, endpoint URLs, rate limits, pricing",
            "Extract code examples for chat completion calls",
            "Compile a quick-reference comparison",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["API", "documentation", "LLM", "OpenAI", "Anthropic", "Gemini"],
    ),
    RealTask(
        task_id="web-014",
        category="web_collect",
        goal="Collect today's top news headlines categorized by World, Tech, Business, and Sports",
        expected_steps=[
            "Search multiple news aggregators (Google News, BBC, Reuters)",
            "Extract headlines and short summaries per category",
            "Deduplicate across sources",
            "Present grouped by category with source links",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["news", "headlines", "aggregation", "daily"],
    ),
    RealTask(
        task_id="web-015",
        category="web_collect",
        goal="Search for software tools for real-time collaborative editing (like Figma or Google Docs)",
        expected_steps=[
            "Search for collaborative editing tools across categories (docs, design, code)",
            "Collect tool name, key features, pricing tiers, platform support",
            "Extract user ratings and review counts",
            "Build a comparison matrix sorted by category",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["software", "tools", "collaboration", "comparison"],
    ),
    RealTask(
        task_id="web-016",
        category="web_collect",
        goal="Find tutorial resources for learning Rust programming for beginners",
        expected_steps=[
            "Search for 'best Rust tutorials for beginners'",
            "Collect free and paid resources: books, video courses, interactive platforms",
            "Extract format, estimated time to complete, prerequisites, user ratings",
            "Rank by beginner-friendliness and community popularity",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["tutorials", "Rust", "programming", "learning"],
    ),
    RealTask(
        task_id="web-017",
        category="web_collect",
        goal="Collect domain-specific terminology and jargon used in the cryptocurrency industry",
        expected_steps=[
            "Search for cryptocurrency glossaries and terminology pages",
            "Extract terms, definitions, and usage context",
            "Categorize by subdomain (DeFi, NFTs, exchanges, mining)",
            "Output as a structured glossary JSON",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["terminology", "cryptocurrency", "glossary", "blockchain"],
    ),
    RealTask(
        task_id="web-018",
        category="web_collect",
        goal="Search for recent US Supreme Court rulings published in 2025",
        expected_steps=[
            "Search for US Supreme Court 2025 opinion list",
            "Navigate to official supremecourt.gov opinions page",
            "Extract case name, docket number, decision date, majority opinion author",
            "Summarize the holding for each ruling",
        ],
        difficulty="hard",
        estimated_duration_s=300,
        tags=["legal", "supreme-court", "rulings", "government"],
    ),
    RealTask(
        task_id="web-019",
        category="web_collect",
        goal="Find patent information for generative AI models filed in 2024–2025",
        expected_steps=[
            "Search patent databases (USPTO, Google Patents) for generative AI patents",
            "Collect patent number, title, assignee, filing date, abstract",
            "Identify key innovators and organizations",
            "Group by technology area (text, image, audio, video generation)",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["patents", "AI", "generative", "IP", "legal"],
    ),
    RealTask(
        task_id="web-020",
        category="web_collect",
        goal="Collect trending topics on social media related to sustainable technology this week",
        expected_steps=[
            "Search for trending discussions on sustainability + technology",
            "Collect hashtags, post counts, key influencers, and article references",
            "Identify top 3 trending narratives or debates",
            "Summarize with source links and sentiment indicators",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["social-media", "trending", "sustainability", "tech"],
    ),

    # =========================================================================
    # 2. PAPER SEARCH (15 tasks)
    # =========================================================================
    RealTask(
        task_id="paper-001",
        category="paper_search",
        goal="Find papers by Yoshua Bengio published in 2024 on the topic of generative models",
        expected_steps=[
            "Search arXiv and Google Scholar for Yoshua Bengio 2024 papers",
            "Filter for generative model related papers",
            "Collect titles, abstracts, arXiv IDs, and citation counts",
            "Sort by citation count descending",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["papers", "author-search", "Bengio", "generative-models"],
    ),
    RealTask(
        task_id="paper-002",
        category="paper_search",
        goal="Search for the 10 most cited recent papers on reinforcement learning published in 2024",
        expected_steps=[
            "Search for recent reinforcement learning papers",
            "Filter by year >= 2024 and sort by citation count",
            "Extract title, authors, venue, citation count",
            "List top 10 with links to full text",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["papers", "reinforcement-learning", "trending", "citations"],
    ),
    RealTask(
        task_id="paper-003",
        category="paper_search",
        goal="Find papers accepted at NeurIPS 2024 on the topic of large language model alignment",
        expected_steps=[
            "Search for NeurIPS 2024 proceedings",
            "Filter papers with 'alignment' or 'LLM alignment' in title/abstract",
            "Collect titles, authors, and abstract summaries",
            "Group by alignment approach (RLHF, constitutional AI, etc.)",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["papers", "NeurIPS", "alignment", "LLM", "conference"],
    ),
    RealTask(
        task_id="paper-004",
        category="paper_search",
        goal="Collect all citations for the paper 'Attention Is All You Need' from 2024–2025",
        expected_steps=[
            "Search for the paper on Semantic Scholar or Google Scholar",
            "Collect citing papers published 2024–2025",
            "Extract title, authors, venue, and year for each citing paper",
            "Analyze trends: which fields cite it most",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["papers", "citations", "attention", "impact-analysis"],
    ),
    RealTask(
        task_id="paper-005",
        category="paper_search",
        goal="Search for papers that use diffusion models for protein structure prediction",
        expected_steps=[
            "Search for protein structure prediction + diffusion models",
            "Collect paper titles, methods sections, benchmark datasets used",
            "Extract reported performance metrics (RMSD, TM-score)",
            "Compare methods and results in a table",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["papers", "diffusion-models", "protein", "bioinformatics"],
    ),
    RealTask(
        task_id="paper-006",
        category="paper_search",
        goal="Find survey papers on the topic of multimodal learning published since 2023",
        expected_steps=[
            "Search for 'survey multimodal learning 2023 2024 2025'",
            "Collect papers that are explicitly surveys/reviews",
            "Extract taxonomy diagrams described, covered modalities, key challenges",
            "Summarize the scope and conclusions of each survey",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["papers", "survey", "multimodal", "review"],
    ),
    RealTask(
        task_id="paper-007",
        category="paper_search",
        goal="Collect open-access papers about adversarial robustness in neural networks",
        expected_steps=[
            "Search for adversarial robustness papers on open-access repositories",
            "Filter for papers with PDF available on arXiv or open-access journals",
            "Extract attack methods studied, defense techniques proposed, benchmarks",
            "Summarize key findings and open challenges",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["papers", "adversarial", "robustness", "open-access"],
    ),
    RealTask(
        task_id="paper-008",
        category="paper_search",
        goal="Search for papers published by the MIT CSAIL department in 2024",
        expected_steps=[
            "Search for MIT CSAIL publications 2024",
            "Browse the CSAIL publications list page",
            "Collect paper titles, authors, venues for the year",
            "Categorize by research group within CSAIL",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["papers", "MIT", "CSAIL", "university"],
    ),
    RealTask(
        task_id="paper-009",
        category="paper_search",
        goal="Find the latest preprints on arXiv about agentic AI systems",
        expected_steps=[
            "Search arXiv for 'agentic AI' or 'AI agents' sorted by date",
            "Extract newest 20 preprints with abstracts and arXiv IDs",
            "Identify common themes and emerging research directions",
            "Summarize with links to each preprint",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["papers", "arXiv", "preprints", "agentic-AI", "agents"],
    ),
    RealTask(
        task_id="paper-010",
        category="paper_search",
        goal="Search for papers with publicly available code implementations on GitHub",
        expected_steps=[
            "Search papers-with-code or GitHub for recent ML papers",
            "Filter for papers with accompanying code repositories",
            "Extract paper title, GitHub stars, framework used, license",
            "Rank by GitHub popularity and recency",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["papers", "code", "GitHub", "reproducibility"],
    ),
    RealTask(
        task_id="paper-011",
        category="paper_search",
        goal="Find papers about the ImageNet dataset: its construction, evolution, and impact",
        expected_steps=[
            "Search for papers discussing ImageNet from 2009 to present",
            "Collect key papers: the original, evolution papers, analysis studies",
            "Extract dataset size changes, class taxonomy, performance milestones",
            "Create a historical timeline with citation counts",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["papers", "ImageNet", "dataset", "history"],
    ),
    RealTask(
        task_id="paper-012",
        category="paper_search",
        goal="Collect papers published between 2020 and 2024 on efficient transformer architectures",
        expected_steps=[
            "Search for efficient transformer papers (Linformer, Performer, FlashAttention etc.)",
            "Filter by publication date 2020–2024",
            "Extract efficiency metrics: FLOPs, memory, speedup, parameter count",
            "Build a comparison table of efficiency vs accuracy trade-offs",
        ],
        difficulty="medium",
        estimated_duration_s=300,
        tags=["papers", "transformers", "efficiency", "architecture"],
    ),
    RealTask(
        task_id="paper-013",
        category="paper_search",
        goal="Search for papers published in Japanese language journals about natural language processing",
        expected_steps=[
            "Search for NLP papers in Japanese language venues",
            "Collect title (Japanese + English translation), authors, journal, year",
            "Extract research topics and key contributions",
            "Translate abstracts to English for comparison",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["papers", "Japanese", "NLP", "language"],
    ),
    RealTask(
        task_id="paper-014",
        category="paper_search",
        goal="Find papers with over 1000 citations published in 2024 in the field of computer vision",
        expected_steps=[
            "Search for highly-cited 2024 computer vision papers",
            "Filter for papers with > 1000 citations",
            "Collect title, first author, venue, citation count, research area",
            "Analyze what made these papers highly influential",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["papers", "high-citation", "computer-vision", "impact"],
    ),
    RealTask(
        task_id="paper-015",
        category="paper_search",
        goal="Collect papers for a literature review on privacy-preserving machine learning techniques",
        expected_steps=[
            "Search for papers on differential privacy, federated learning, secure multi-party computation",
            "Collect 30+ relevant papers with abstracts and publication venues",
            "Categorize by privacy technique and application domain",
            "Extract key results and open problems for each category",
            "Generate a BibTeX bibliography file",
        ],
        difficulty="hard",
        estimated_duration_s=480,
        tags=["papers", "literature-review", "privacy", "ML"],
    ),

    # =========================================================================
    # 3. NEWS COLLECTION (15 tasks)
    # =========================================================================
    RealTask(
        task_id="news-001",
        category="news_collect",
        goal="Collect today's top 10 headlines from BBC, CNN, and Reuters",
        expected_steps=[
            "Visit BBC homepage, CNN homepage, Reuters homepage",
            "Extract top headlines from each",
            "Deduplicate across sources",
            "Present as a unified Top 10 list with source attribution",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["news", "headlines", "BBC", "CNN", "Reuters"],
    ),
    RealTask(
        task_id="news-002",
        category="news_collect",
        goal="Find all news articles about Nvidia published in the last 7 days",
        expected_steps=[
            "Search for 'Nvidia' in Google News filtered to past week",
            "Collect article headlines, dates, sources, URLs",
            "Extract key themes (products, stock, partnerships, lawsuits)",
            "Group articles by theme with brief summaries",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["news", "Nvidia", "company", "weekly"],
    ),
    RealTask(
        task_id="news-003",
        category="news_collect",
        goal="Search for the latest technology news about quantum computing breakthroughs",
        expected_steps=[
            "Search for 'quantum computing breakthrough 2025'",
            "Collect recent articles from tech news sites and scientific journals",
            "Extract the specific breakthrough claimed, institution, validation status",
            "Rank by significance and credibility of source",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["news", "technology", "quantum-computing", "breakthroughs"],
    ),
    RealTask(
        task_id="news-004",
        category="news_collect",
        goal="Gather the most important science news from the past week across astronomy, medicine, and physics",
        expected_steps=[
            "Search top science news sites (Nature, Science, New Scientist) from past 7 days",
            "Categorize articles by astronomy, medicine, physics",
            "Extract key findings and significance for each",
            "Summarize the week's top 5 science stories",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["news", "science", "weekly-roundup", "astronomy", "medicine", "physics"],
    ),
    RealTask(
        task_id="news-005",
        category="news_collect",
        goal="Find local news for the city of Seattle from the past 48 hours",
        expected_steps=[
            "Search for 'Seattle local news today'",
            "Visit Seattle Times, Crosscut, and local news aggregators",
            "Extract headlines about local government, events, traffic, weather",
            "Summarize top 5 local stories",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["news", "local", "Seattle", "city"],
    ),
    RealTask(
        task_id="news-006",
        category="news_collect",
        goal="Collect news about the renewable energy industry from the past month",
        expected_steps=[
            "Search for renewable energy news (solar, wind, hydro, storage)",
            "Collect articles published in the last 30 days",
            "Extract company announcements, policy changes, technology milestones",
            "Categorize by energy type and geographic region",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["news", "renewable-energy", "industry", "monthly"],
    ),
    RealTask(
        task_id="news-007",
        category="news_collect",
        goal="Find opinion pieces discussing the societal impact of artificial general intelligence",
        expected_steps=[
            "Search for opinion/editorial articles on AGI impact",
            "Filter for op-eds from reputable sources and known thinkers",
            "Extract the author's main argument and stance (optimistic/cautious)",
            "Compile a spectrum of viewpoints with source links",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["news", "opinion", "AGI", "editorial"],
    ),
    RealTask(
        task_id="news-008",
        category="news_collect",
        goal="Find investigative journalism pieces about Big Tech antitrust enforcement in 2025",
        expected_steps=[
            "Search for investigative reporting on Big Tech antitrust",
            "Look at ProPublica, The Markup, Reuters Investigations",
            "Extract key findings, named entities (companies, regulators), dates",
            "Summarize the investigation's core allegations and evidence",
        ],
        difficulty="hard",
        estimated_duration_s=300,
        tags=["news", "investigative", "antitrust", "Big-Tech"],
    ),
    RealTask(
        task_id="news-009",
        category="news_collect",
        goal="Collect news coverage from at least 5 different sources about the same major event",
        expected_steps=[
            "Identify a major current event",
            "Search for coverage on BBC, CNN, Al Jazeera, Reuters, and The Guardian",
            "Extract each source's headline, framing angle, and key facts",
            "Compare how coverage differs across outlets and identify common ground",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["news", "multi-source", "comparison", "media-analysis"],
    ),
    RealTask(
        task_id="news-010",
        category="news_collect",
        goal="Search for editorials about education reform in the US from major newspapers",
        expected_steps=[
            "Search for 'education reform editorial' from NYT, WaPo, WSJ, The Atlantic",
            "Collect editorial titles, dates, and author positions",
            "Extract proposed reforms and supporting arguments",
            "Compare editorial stances across publications",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["news", "editorials", "education", "reform"],
    ),
    RealTask(
        task_id="news-011",
        category="news_collect",
        goal="Find business news about IPO filings and public offerings in the tech sector this quarter",
        expected_steps=[
            "Search for 'tech IPO 2025' and 'new public offerings'",
            "Collect company names, filing dates, exchange, target valuation",
            "Extract business descriptions and underwriter details",
            "Compile a quarterly IPO tracker table",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["news", "business", "IPO", "tech", "finance"],
    ),
    RealTask(
        task_id="news-012",
        category="news_collect",
        goal="Collect health and medical news about mRNA vaccine developments beyond COVID-19",
        expected_steps=[
            "Search for mRNA vaccine news (flu, RSV, cancer, etc.)",
            "Collect recent articles from medical journals and health news sites",
            "Extract clinical trial phases, efficacy data, regulatory status",
            "Categorize by disease target with timeline of progress",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["news", "health", "vaccines", "mRNA", "medical"],
    ),
    RealTask(
        task_id="news-013",
        category="news_collect",
        goal="Search for environmental news about biodiversity loss and conservation efforts in 2025",
        expected_steps=[
            "Search for 'biodiversity conservation 2025' news",
            "Collect articles from environmental news sites and NGO reports",
            "Extract species at risk, conservation measures, policy actions",
            "Categorize by region and conservation approach",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["news", "environment", "biodiversity", "conservation"],
    ),
    RealTask(
        task_id="news-014",
        category="news_collect",
        goal="Find sports news covering the latest transfer window activity in European football",
        expected_steps=[
            "Search for '2025 summer transfer window news'",
            "Collect confirmed transfers, rumors, fees from ESPN, Sky Sports, BBC Sport",
            "Extract player name, clubs involved, transfer fee, contract duration",
            "Organize by league with notable transfers highlighted",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["news", "sports", "football", "transfers"],
    ),
    RealTask(
        task_id="news-015",
        category="news_collect",
        goal="Collect entertainment news about major movie releases and box office performance this summer",
        expected_steps=[
            "Search for summer 2025 movie releases and box office results",
            "Collect opening weekend numbers, budgets, Rotten Tomatoes scores",
            "Extract release dates, studios, directors, cast",
            "Compare box office performance and critical reception",
        ],
        difficulty="easy",
        estimated_duration_s=120,
        tags=["news", "entertainment", "movies", "box-office", "summer"],
    ),

    # =========================================================================
    # 4. DATA EXTRACTION (15 tasks)
    # =========================================================================
    RealTask(
        task_id="data-001",
        category="data_extract",
        goal="Extract all tables from a Wikipedia page about country GDP data",
        expected_steps=[
            "Open Wikipedia page for 'List of countries by GDP (nominal)'",
            "Identify HTML table elements containing GDP data",
            "Extract each table as structured rows with column headers",
            "Output as CSV with rows as country-year entries",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["extraction", "tables", "Wikipedia", "GDP"],
    ),
    RealTask(
        task_id="data-002",
        category="data_extract",
        goal="Extract structured data (product name, price, rating) from a paginated e-commerce listing page",
        expected_steps=[
            "Browse an e-commerce category page through multiple pages",
            "Extract product name, price, rating, review count per item",
            "Handle pagination to collect all items",
            "Output as a structured JSON array",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["extraction", "e-commerce", "pagination", "products"],
    ),
    RealTask(
        task_id="data-003",
        category="data_extract",
        goal="Convert unstructured text of a product description into a structured format with fields",
        expected_steps=[
            "Read free-form product description text",
            "Identify and extract fields: brand, model, dimensions, weight, color, material",
            "Normalize units and formats",
            "Output as structured key-value pairs",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["extraction", "unstructured", "structured", "NLP"],
    ),
    RealTask(
        task_id="data-004",
        category="data_extract",
        goal="Extract all email addresses and phone numbers from a directory web page",
        expected_steps=[
            "Fetch the directory webpage",
            "Use regex or DOM traversal to find email addresses and phone numbers",
            "Remove duplicates and validate format",
            "Output as a JSON list grouped by type",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["extraction", "contact", "PII", "regex"],
    ),
    RealTask(
        task_id="data-005",
        category="data_extract",
        goal="Parse search engine results pages (SERPs) and organize the top 20 results into a structured format",
        expected_steps=[
            "Execute a search query on a search engine",
            "Extract title, URL, snippet text for each result",
            "Identify result type (organic, ad, featured snippet, knowledge panel)",
            "Output structured list ranked by position",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["extraction", "SERP", "search", "parsing"],
    ),
    RealTask(
        task_id="data-006",
        category="data_extract",
        goal="Extract key financial metrics (revenue, profit, margin) from a company's annual report PDF or HTML",
        expected_steps=[
            "Open the annual report page for a company",
            "Locate the financial highlights section",
            "Extract year-over-year key metrics",
            "Calculate growth percentages and output as a table",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["extraction", "finance", "annual-report", "metrics"],
    ),
    RealTask(
        task_id="data-007",
        category="data_extract",
        goal="Extract a timeline of events from a series of news articles about a developing story",
        expected_steps=[
            "Collect multiple articles about a developing news story",
            "Identify temporal expressions and event descriptions in each article",
            "Resolve relative dates to absolute dates",
            "Construct a chronological timeline with source attribution",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["extraction", "timeline", "events", "temporal"],
    ),
    RealTask(
        task_id="data-008",
        category="data_extract",
        goal="Extract product features (specs, included items, warranty) from multiple product listing pages",
        expected_steps=[
            "Browse product listing pages for similar items",
            "Extract feature lists, specifications tables, included accessories",
            "Normalize feature names across products",
            "Build a feature comparison matrix",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["extraction", "product-features", "comparison", "specs"],
    ),
    RealTask(
        task_id="data-009",
        category="data_extract",
        goal="Extract location data (addresses, coordinates, places) from a set of restaurant descriptions",
        expected_steps=[
            "Read restaurant description texts or HTML",
            "Extract full addresses and geographic coordinates",
            "Identify cuisine type and price range associated with each location",
            "Output as a geocoded JSON array suitable for mapping",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["extraction", "location", "geocoding", "restaurants"],
    ),
    RealTask(
        task_id="data-010",
        category="data_extract",
        goal="Extract all numerical data points from a scientific research article (tables, figures, statistics)",
        expected_steps=[
            "Open a scientific article in HTML or PDF form",
            "Identify numerical data in tables, figures captions, and statistics sections",
            "Extract values with their units and context labels",
            "Organize by figure/table number with descriptions",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["extraction", "numerical", "scientific", "research-article"],
    ),
    RealTask(
        task_id="data-011",
        category="data_extract",
        goal="Extract all named entities (persons, organizations, locations) from a news article",
        expected_steps=[
            "Read a news article text",
            "Identify person names, organization names, location names",
            "Categorize each entity by type",
            "Output as a structured list with entity frequency counts",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["extraction", "NER", "named-entities", "NLP"],
    ),
    RealTask(
        task_id="data-012",
        category="data_extract",
        goal="Extract relationships between entities from a set of business partnership announcements",
        expected_steps=[
            "Collect press releases about company partnerships",
            "Identify entity pairs (Company A partnerships with Company B)",
            "Extract relationship type (joint venture, acquisition, supply agreement)",
            "Build a knowledge graph of company relationships",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["extraction", "relationships", "entities", "knowledge-graph"],
    ),
    RealTask(
        task_id="data-013",
        category="data_extract",
        goal="Extract chronological sequences of events from a historical documentary transcript",
        expected_steps=[
            "Read a documentary transcript",
            "Identify event descriptions with temporal markers",
            "Order events chronologically, resolving relative time references",
            "Output as a structured timeline with event descriptions and timestamps",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["extraction", "chronological", "timeline", "transcript"],
    ),
    RealTask(
        task_id="data-014",
        category="data_extract",
        goal="Extract comparative data from product review articles comparing competing products",
        expected_steps=[
            "Find comparison review articles (e.g., 'iPhone vs Pixel vs Galaxy')",
            "Extract comparison criteria, scores, and winner per category",
            "Extract quoted pros and cons for each product",
            "Build a weighted comparison table",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["extraction", "comparison", "reviews", "products"],
    ),
    RealTask(
        task_id="data-015",
        category="data_extract",
        goal="Extract summary statistics (mean, median, min, max, stddev) from a dataset file",
        expected_steps=[
            "Read a data file (CSV or JSON)",
            "Identify numeric columns",
            "Calculate descriptive statistics for each column",
            "Output as a statistics summary table",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["extraction", "statistics", "summary", "data-analysis"],
    ),

    # =========================================================================
    # 5. FILE OPERATIONS (15 tasks)
    # =========================================================================
    RealTask(
        task_id="file-001",
        category="file_ops",
        goal="Read a text file and produce a 3-sentence summary of its contents",
        expected_steps=[
            "Read the specified file",
            "Analyze content for main topics and key points",
            "Generate a concise 3-sentence summary",
            "Output the summary to stdout",
        ],
        difficulty="easy",
        estimated_duration_s=30,
        tags=["file", "read", "summarize", "NLP"],
    ),
    RealTask(
        task_id="file-002",
        category="file_ops",
        goal="Search for all occurrences of the regex pattern '\\d{3}-\\d{4}' across all text files in a directory",
        expected_steps=[
            "List all text files in the directory",
            "Read each file and apply the regex pattern",
            "Collect matching lines with file name and line number",
            "Output results grouped by file",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["file", "search", "regex", "pattern-matching"],
    ),
    RealTask(
        task_id="file-003",
        category="file_ops",
        goal="Compare two CSV files and report rows that differ",
        expected_steps=[
            "Read both CSV files with headers",
            "Identify matching key column(s)",
            "Compare row by row based on key",
            "Report added, removed, and changed rows with diff details",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["file", "compare", "diff", "CSV"],
    ),
    RealTask(
        task_id="file-004",
        category="file_ops",
        goal="Merge three CSV files with the same columns into one combined file",
        expected_steps=[
            "Read all three CSV files and verify identical column headers",
            "Concatenate rows, preserving a source file origin column",
            "Remove duplicate rows if any",
            "Write the merged result to a new file",
        ],
        difficulty="easy",
        estimated_duration_s=45,
        tags=["file", "merge", "CSV", "combine"],
    ),
    RealTask(
        task_id="file-005",
        category="file_ops",
        goal="Convert a JSON file to CSV format with flattened nested fields",
        expected_steps=[
            "Read the JSON file and analyze its structure",
            "Identify top-level and nested fields to flatten",
            "Flatten nested objects with dot notation keys",
            "Write as CSV with appropriate headers",
        ],
        difficulty="medium",
        estimated_duration_s=90,
        tags=["file", "convert", "JSON", "CSV", "flatten"],
    ),
    RealTask(
        task_id="file-006",
        category="file_ops",
        goal="Extract lines 100-200 from a very large log file and save to a new file",
        expected_steps=[
            "Open the large log file",
            "Read lines 100 to 200 (1-indexed)",
            "Write the extracted lines to a new file",
            "Report line count and file size of output",
        ],
        difficulty="easy",
        estimated_duration_s=30,
        tags=["file", "extract", "lines", "log"],
    ),
    RealTask(
        task_id="file-007",
        category="file_ops",
        goal="Count occurrences of each unique word in a text file and produce a frequency table",
        expected_steps=[
            "Read the file and tokenize into words",
            "Normalize case and remove punctuation",
            "Count frequencies using a dictionary",
            "Output sorted by frequency descending (top N)",
        ],
        difficulty="easy",
        estimated_duration_s=45,
        tags=["file", "count", "word-frequency", "analysis"],
    ),
    RealTask(
        task_id="file-008",
        category="file_ops",
        goal="Sort a CSV file by a specified column and write the sorted output to a new file",
        expected_steps=[
            "Read the CSV file with headers",
            "Identify the column to sort by",
            "Sort rows (handle numeric vs string columns appropriately)",
            "Write sorted output preserving header row",
        ],
        difficulty="easy",
        estimated_duration_s=45,
        tags=["file", "sort", "CSV", "organize"],
    ),
    RealTask(
        task_id="file-009",
        category="file_ops",
        goal="Validate that a JSON file conforms to a given JSON Schema specification",
        expected_steps=[
            "Read the JSON data file",
            "Parse the JSON Schema definition",
            "Validate each field type, required fields, and constraints",
            "Report validation errors with file paths and line numbers",
        ],
        difficulty="medium",
        estimated_duration_s=120,
        tags=["file", "validate", "JSON", "schema"],
    ),
    RealTask(
        task_id="file-010",
        category="file_ops",
        goal="Find duplicate content across multiple text files in a project directory",
        expected_steps=[
            "List all text/code files in the project",
            "Compute a hash (MD5 or SHA256) of each file",
            "Group files by hash to identify duplicates",
            "Report duplicate groups with file paths and sizes",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["file", "duplicates", "hash", "deduplication"],
    ),
    RealTask(
        task_id="file-011",
        category="file_ops",
        goal="Generate a Markdown report from a data file summarizing key statistics",
        expected_steps=[
            "Read a structured data file (CSV/JSON)",
            "Compute summary statistics per column/field",
            "Generate a Markdown document with tables, bullet points, headings",
            "Write the report to a .md file",
        ],
        difficulty="medium",
        estimated_duration_s=120,
        tags=["file", "report", "Markdown", "statistics"],
    ),
    RealTask(
        task_id="file-012",
        category="file_ops",
        goal="Split a large CSV file into multiple smaller files of at most 1000 rows each",
        expected_steps=[
            "Read the CSV file and count total rows",
            "Calculate number of splits needed (1000 rows per chunk)",
            "Write each chunk to a separate file preserving header row",
            "Name files sequentially (file_part1.csv, file_part2.csv, ...)",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["file", "split", "CSV", "chunking"],
    ),
    RealTask(
        task_id="file-013",
        category="file_ops",
        goal="Combine multiple Markdown files into one document with a table of contents",
        expected_steps=[
            "List all .md files in the directory",
            "Read each file and extract heading hierarchy",
            "Generate a table of contents linking to headings",
            "Concatenate all content with TOC at the top",
        ],
        difficulty="medium",
        estimated_duration_s=120,
        tags=["file", "combine", "Markdown", "TOC"],
    ),
    RealTask(
        task_id="file-014",
        category="file_ops",
        goal="Search for all files containing the word 'TODO' or 'FIXME' in a codebase",
        expected_steps=[
            "Recursively list all source files in the codebase",
            "Read each file and search for TODO/FIXME patterns",
            "Extract the line content with file path and line number",
            "Group results by file and report count per file",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["file", "search", "TODO", "code-quality"],
    ),
    RealTask(
        task_id="file-015",
        category="file_ops",
        goal="Archive log files older than 30 days into a compressed tar.gz archive",
        expected_steps=[
            "List all log files in the logs directory",
            "Check modification dates against 30-day threshold",
            "Compress old files into a timestamped archive",
            "Optionally delete originals after successful archiving",
        ],
        difficulty="medium",
        estimated_duration_s=90,
        tags=["file", "archive", "logs", "cleanup"],
    ),

    # =========================================================================
    # 6. MULTI-STEP TASKS (10 tasks)
    # =========================================================================
    RealTask(
        task_id="multi-001",
        category="multi_step",
        goal="Search for the cheapest flights from NYC to London in May → collect results → extract prices → summarize best deal",
        expected_steps=[
            "Search for flight options from NYC to London May 2025",
            "Collect flight details from travel aggregators",
            "Extract prices, airlines, duration, number of stops",
            "Compare and identify the best value option",
            "Summarize with price and booking link",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["multi-step", "flights", "travel", "comparison"],
    ),
    RealTask(
        task_id="multi-002",
        category="multi_step",
        goal="Read three different news articles about a company → compare coverage → produce a balanced report",
        expected_steps=[
            "Search for recent news about a specified company",
            "Select 3 articles from different sources",
            "Read and extract key facts, quotes, and angles",
            "Compare framing and identify biases",
            "Produce a balanced summary report in Markdown",
        ],
        difficulty="medium",
        estimated_duration_s=300,
        tags=["multi-step", "news-analysis", "comparison", "report"],
    ),
    RealTask(
        task_id="multi-003",
        category="multi_step",
        goal="Search for a research dataset → download it → convert format → validate integrity",
        expected_steps=[
            "Search for an open dataset matching specified criteria",
            "Download the dataset via direct URL or API",
            "Convert from original format (e.g., XML → CSV)",
            "Validate row count, column types, and checksum",
            "Report conversion summary with validation results",
        ],
        difficulty="hard",
        estimated_duration_s=480,
        tags=["multi-step", "dataset", "download", "convert", "validate"],
    ),
    RealTask(
        task_id="multi-004",
        category="multi_step",
        goal="Browse a documentation website → navigate through subpages → collect code examples → organize by topic",
        expected_steps=[
            "Open the documentation homepage for a framework",
            "Navigate through getting-started, guides, and API reference sections",
            "Collect code snippets and example blocks from each page",
            "Organize by topic with source URL and description",
            "Output as a consolidated cheat-sheet file",
        ],
        difficulty="medium",
        estimated_duration_s=300,
        tags=["multi-step", "documentation", "scrape", "code-examples"],
    ),
    RealTask(
        task_id="multi-005",
        category="multi_step",
        goal="Search for a file in multiple directories → if first attempt fails → retry with broader search → verify file integrity",
        expected_steps=[
            "Search for a specified file name in primary directories",
            "If not found, expand search to parent directories and common locations",
            "When found, verify file is not empty and has expected extension",
            "Report the file path, size, and last modified date",
        ],
        difficulty="easy",
        estimated_duration_s=90,
        tags=["multi-step", "search", "retry", "recovery"],
    ),
    RealTask(
        task_id="multi-006",
        category="multi_step",
        goal="Collect search results from multiple queries → deduplicate → sort by relevance → present in a ranked list",
        expected_steps=[
            "Execute multiple related search queries",
            "Collect all results with titles, snippets, and URLs",
            "Deduplicate entries pointing to the same URL",
            "Score by relevance and recency, then sort descending",
            "Present as a ranked, annotated list",
        ],
        difficulty="medium",
        estimated_duration_s=180,
        tags=["multi-step", "search", "deduplicate", "rank"],
    ),
    RealTask(
        task_id="multi-007",
        category="multi_step",
        goal="Search for a product → validate reviews and ratings → enrich with price history → export as PDF report",
        expected_steps=[
            "Search for a specific product across e-commerce sites",
            "Collect and validate reviews (check for fake review indicators)",
            "Fetch price history data from tracking sites",
            "Enrich product listing with price trend analysis",
            "Generate and export a PDF report with findings",
        ],
        difficulty="hard",
        estimated_duration_s=480,
        tags=["multi-step", "product", "reviews", "pricing", "report"],
    ),
    RealTask(
        task_id="multi-008",
        category="multi_step",
        goal="Read a CSV data file → analyze for trends → extract insights → write a findings report",
        expected_steps=[
            "Read and parse the CSV data file",
            "Perform exploratory data analysis (summary stats, correlations)",
            "Identify trends, anomalies, and key insights",
            "Write a structured findings report with charts (markdown tables)",
            "Save the report alongside the original data",
        ],
        difficulty="medium",
        estimated_duration_s=240,
        tags=["multi-step", "data-analysis", "insights", "report"],
    ),
    RealTask(
        task_id="multi-009",
        category="multi_step",
        goal="Monitor a web page for changes → detect a specific update → send an alert with change summary",
        expected_steps=[
            "Fetch the current state of a specified web page",
            "Compare with a previously stored snapshot",
            "Detect if a specific keyword or section has changed",
            "Generate a diff summary of detected changes",
            "Output an alert message with the change description",
        ],
        difficulty="hard",
        estimated_duration_s=300,
        tags=["multi-step", "monitor", "diff", "alert"],
    ),
    RealTask(
        task_id="multi-010",
        category="multi_step",
        goal="Plan how to find a specific academic paper → execute the search → reflect on search results → adjust query → re-execute",
        expected_steps=[
            "Plan search strategy: choose databases and keywords",
            "Execute initial search on academic databases",
            "Evaluate results: relevant vs. irrelevant",
            "Reflect on keyword effectiveness and adjust query terms",
            "Re-execute with refined query and collect results",
        ],
        difficulty="hard",
        estimated_duration_s=360,
        tags=["multi-step", "search", "reflection", "iterative"],
    ),

    # =========================================================================
    # 7. RECOVERY TASKS (10 tasks)
    # =========================================================================
    RealTask(
        task_id="rec-001",
        category="recovery",
        goal="Retry a failed network request 3 times with exponential backoff before giving up",
        expected_steps=[
            "Attempt the network request",
            "Detect failure (timeout, connection error, HTTP 5xx)",
            "Wait with exponential backoff (1s, 2s, 4s) between retries",
            "Retry up to 3 times",
            "If all fail, report the error with retry history",
        ],
        difficulty="easy",
        estimated_duration_s=30,
        tags=["recovery", "network", "retry", "backoff"],
    ),
    RealTask(
        task_id="rec-002",
        category="recovery",
        goal="Recover from a timeout when scraping a large page: retry with a shorter timeout setting",
        expected_steps=[
            "Attempt to scrape a page with default timeout",
            "Catch TimeoutError or timeout-related exception",
            "Reduce timeout parameter and retry",
            "If partial content received, save what was retrieved",
            "Log the recovery action taken",
        ],
        difficulty="medium",
        estimated_duration_s=45,
        tags=["recovery", "timeout", "scrape", "fallback"],
    ),
    RealTask(
        task_id="rec-003",
        category="recovery",
        goal="Fall back to an alternative search engine when the primary search tool returns no results",
        expected_steps=[
            "Execute search on primary search engine",
            "Detect empty results or error response",
            "Switch to alternative search engine as fallback",
            "Re-execute the same query on the fallback",
            "Return results annotated with which engine was used",
        ],
        difficulty="easy",
        estimated_duration_s=60,
        tags=["recovery", "fallback", "search", "resilience"],
    ),
    RealTask(
        task_id="rec-004",
        category="recovery",
        goal="Resume an interrupted data collection task from the last successfully processed item",
        expected_steps=[
            "Detect that a prior collection task was interrupted",
            "Read checkpoint/save file to find last completed item",
            "Resume collection from the next item",
            "Verify no gaps or duplicate items collected",
            "Update checkpoint as new items are processed",
        ],
        difficulty="hard",
        estimated_duration_s=120,
        tags=["recovery", "resume", "checkpoint", "collection"],
    ),
    RealTask(
        task_id="rec-005",
        category="recovery",
        goal="Clean up temporary files after a partial failure in a file processing pipeline",
        expected_steps=[
            "Detect that a prior file processing step failed",
            "Identify temporary/intermediate files left behind",
            "Remove temp files to free disk space",
            "Log what was cleaned up for audit",
            "Report the cleanup actions taken",
        ],
        difficulty="easy",
        estimated_duration_s=30,
        tags=["recovery", "cleanup", "temp-files", "failure"],
    ),
    RealTask(
        task_id="rec-006",
        category="recovery",
        goal="Re-verify data integrity after a write operation encounters intermittent errors",
        expected_steps=[
            "Detect that a write operation had partial errors",
            "Re-read the data that was written",
            "Compare read data with source data for integrity",
            "Re-write any corrupted or missing portions",
            "Return success/failure status with integrity report",
        ],
        difficulty="medium",
        estimated_duration_s=90,
        tags=["recovery", "verify", "integrity", "write-error"],
    ),
    RealTask(
        task_id="rec-007",
        category="recovery",
        goal="Use an alternative tool when the primary tool for a task is unavailable or fails",
        expected_steps=[
            "Attempt to use the primary tool for a task",
            "Detect that the tool is unavailable or fails",
            "Select an alternative tool with equivalent capability",
            "Execute the same task with the alternative tool",
            "Return results with note about which tool succeeded",
        ],
        difficulty="medium",
        estimated_duration_s=60,
        tags=["recovery", "alternative-tool", "fallback", "resilience"],
    ),
    RealTask(
        task_id="rec-008",
        category="recovery",
        goal="Recover from bad/malformed data by sanitizing inputs and re-parsing",
        expected_steps=[
            "Attempt to parse input data (JSON, CSV, etc.)",
            "Catch parse errors and identify malformed segments",
            "Apply sanitization: fix encoding, trim whitespace, escape special chars",
            "Re-attempt parsing on cleaned data",
            "Report what was sanitized and whether parsing succeeded",
        ],
        difficulty="medium",
        estimated_duration_s=90,
        tags=["recovery", "data-quality", "sanitize", "parse"],
    ),
    RealTask(
        task_id="rec-009",
        category="recovery",
        goal="Re-validate extracted data after an intermediate cache was cleared",
        expected_steps=[
            "Detect that a cached result was invalidated or cleared",
            "Identify which operations depended on the cleared cache",
            "Re-execute the source queries or extractions to rebuild cache",
            "Compare re-validated data with original expectations",
            "Report cache rebuild status and any data discrepancies",
        ],
        difficulty="hard",
        estimated_duration_s=120,
        tags=["recovery", "cache", "revalidate", "consistency"],
    ),
    RealTask(
        task_id="rec-010",
        category="recovery",
        goal="Restart a multi-step workflow from the last successful checkpoint after an unexpected crash",
        expected_steps=[
            "Detect that a prior workflow did not complete",
            "Read checkpoint file to determine last completed step",
            "Restore any necessary state from checkpoint",
            "Resume execution from the step after the checkpoint",
            "Verify that resumed execution produces consistent results",
        ],
        difficulty="hard",
        estimated_duration_s=180,
        tags=["recovery", "checkpoint", "restart", "workflow"],
    ),
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_COUNT = len(TASKS)

# Verify we hit 100+
assert TASK_COUNT >= 100, f"TASK_COUNT={TASK_COUNT}, need >= 100"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_tasks() -> list[RealTask]:
    """Return the complete corpus of real-world tasks."""
    return list(TASKS)


def get_tasks_by_category(category: str) -> list[RealTask]:
    """Return all tasks belonging to a given category.

    Args:
        category: One of CATEGORIES.

    Returns:
        Filtered list of RealTask instances.
    """
    assert category in CATEGORIES, f"Unknown category: {category}. Valid: {CATEGORIES}"
    return [t for t in TASKS if t.category == category]


def get_random_tasks(n: int = 10) -> list[RealTask]:
    """Return a random sample of *n* tasks from the corpus.

    Args:
        n: Number of tasks to return (default 10).

    Returns:
        Randomly sampled list of RealTask instances.
    """
    return random.sample(TASKS, min(n, TASK_COUNT))


# ---------------------------------------------------------------------------
# Simple self-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Real Task Corpus: {TASK_COUNT} tasks across {len(CATEGORIES)} categories\n")

    cat_counts = {c: len(get_tasks_by_category(c)) for c in CATEGORIES}
    for cat, count in cat_counts.items():
        print(f"  {cat:20s} → {count:3d} tasks")
    print(f"\n  {'TOTAL':20s} → {TASK_COUNT:3d} tasks")
    print()

    # Verify a few random tasks
    sample = get_random_tasks(3)
    for t in sample:
        print(f"  [{t.task_id}] ({t.difficulty}) {t.goal}")
        print(f"           Category: {t.category}, Tags: {t.tags}")
        print(f"           Steps: {len(t.expected_steps)} steps, ~{t.estimated_duration_s}s")
        print()

    print("All validations passed.")
