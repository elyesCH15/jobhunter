"""Canned API responses shaped like the real Greenhouse / Lever / Ashby / RemoteOK / Arbeitnow /
Adzuna payloads."""

GREENHOUSE_CRADLE = {"jobs": [
    {"id": 101, "title": "Machine Learning Engineer, Protein Language Models",
     "location": {"name": "Amsterdam, Netherlands"}, "absolute_url": "https://boards.greenhouse.io/cradle/jobs/101",
     "updated_at": "2026-10-21T08:00:00-04:00", "company_name": "Cradle",
     "content": "&lt;p&gt;We are looking for an engineer to fine-tune protein language models (ESM) for "
                "protein design and structure prediction. You will work with PyTorch on large GPU clusters (HPC). "
                "Graduate or junior profiles welcome. Full-time position.&lt;/p&gt;"},
    {"id": 102, "title": "Senior Machine Learning Engineer, Platform",
     "location": {"name": "Zurich, Switzerland"}, "absolute_url": "https://boards.greenhouse.io/cradle/jobs/102",
     "updated_at": "2026-10-20T08:00:00Z", "company_name": "Cradle",
     "content": "&lt;p&gt;Senior role. 8+ years of experience building distributed backend systems in Go. "
                "You will lead the machine learning platform team.&lt;/p&gt;"},
    {"id": 103, "title": "Office Manager", "location": {"name": "Amsterdam"},
     "absolute_url": "https://boards.greenhouse.io/cradle/jobs/103", "updated_at": "2026-10-20T08:00:00Z",
     "content": "&lt;p&gt;Run our office and events.&lt;/p&gt;"},
]}

LEVER_OWKIN = [
    {"id": "lv-1", "text": "Stage - Data Scientist Computational Biology (6 mois)",
     "categories": {"location": "Paris, France", "commitment": "Internship"},
     "hostedUrl": "https://jobs.lever.co/owkin/lv-1", "applyUrl": "https://jobs.lever.co/owkin/lv-1/apply",
     "createdAt": 1792569600000,
     "descriptionPlain": "Nous recherchons un stagiaire en biologie computationnelle pour rejoindre notre équipe. "
                         "Vous travaillerez sur des modèles de deep learning pour la découverte de médicaments "
                         "(drug discovery) avec Python et PyTorch. Le poste est basé à Paris.",
     "lists": [{"text": "Profil", "content": "<li>Master ou école d'ingénieur</li><li>bioinformatics</li>"}]},
]

ASHBY_SYLVERA = {"jobs": [
    {"id": "ab-1", "title": "Junior Geospatial Data Scientist", "location": "Remote - Europe",
     "employmentType": "FullTime", "isRemote": True, "isListed": True,
     "jobUrl": "https://jobs.ashbyhq.com/sylvera/ab-1", "applyUrl": "https://jobs.ashbyhq.com/sylvera/ab-1/application",
     "publishedAt": "2026-10-21T06:00:00Z",
     "descriptionPlain": "Use satellite imagery, remote sensing and Google Earth Engine to rate carbon credit "
                         "projects (deforestation, biodiversity). Python, machine learning, GIS."},
]}

REMOTEOK = [
    {"legal": "API terms notice"},
    {"id": "ro-1", "position": "Junior Machine Learning Engineer (LLM / RAG)", "company": "Acme AI",
     "location": "Worldwide", "date": "2026-10-21T07:00:00+00:00", "url": "https://remoteok.com/remote-jobs/ro-1",
     "apply_url": "https://remoteok.com/l/ro-1", "tags": ["python", "llm", "rag"], "salary_min": 50000, "salary_max": 70000,
     "description": "<p>Build RAG pipelines and fine-tune LLM models with PyTorch and Hugging Face. Docker, MLOps.</p>"},
    {"id": "ro-2", "position": "Marketing Coordinator", "company": "Shoes Inc", "location": "Worldwide",
     "date": "2026-10-21T07:00:00+00:00", "url": "https://remoteok.com/remote-jobs/ro-2", "tags": ["marketing"],
     "description": "<p>Social media and campaigns.</p>"},
]

ARBEITNOW = {"data": [
    {"slug": "an-1", "company_name": "KI Werk GmbH", "title": "Machine Learning Engineer (m/w/d)",
     "location": "Berlin", "remote": False, "url": "https://www.arbeitnow.com/jobs/an-1", "tags": ["python"],
     "job_types": ["full time"], "created_at": 1792569600,
     "description": "<p>Wir suchen einen Machine Learning Engineer für unser Team. Sie entwickeln Modelle mit "
                    "Python und PyTorch. Erfahrung mit LLM ist ein Plus. Wir bieten ein tolles Team und "
                    "flexible Arbeitszeiten.</p>"},
    # same Cradle job, reposted on Arbeitnow → must merge with greenhouse:101
    {"slug": "an-2", "company_name": "Cradle B.V.", "title": "Machine Learning Engineer - Protein Language Models",
     "location": "Amsterdam", "remote": False, "url": "https://www.arbeitnow.com/jobs/an-2", "tags": [],
     "job_types": ["full time"], "created_at": 1792569600,
     "description": "<p>Fine-tune protein language models.</p>"},
]}

ADZUNA_GB = {"results": [
    {"id": 9001, "title": "Graduate AI Engineer - Computer Vision", "company": {"display_name": "VisionCo"},
     "location": {"display_name": "London, UK"}, "redirect_url": "https://adzuna.co.uk/9001",
     "created": "2026-10-20T10:00:00Z", "contract_time": "full_time",
     "description": "Graduate role building computer vision and image generation models (diffusion, Stable Diffusion, "
                    "segmentation) in PyTorch."},
]}
