# LinkedIn post draft

> Draft only. Review before posting; every number links back to a file in the repo.

---

I built jobpulse, a daily career-agent loop for my Summer 2027 internship search. It's open source.

Every day it pulls new SWE/AI internship postings, uses an LLM (Qwen) to extract each job's required skills, tracks demand and trends, tailors a one-page resume per posting, and proposes portfolio projects for my skill gaps.

The rule I cared most about: the resume tailor only selects and reorders bullets I already wrote. It never generates text. A property test checked 2,000 generated variants with 0 violations.

What I measured:
• Skill extraction on 30 blind-labeled job descriptions: F1 0.946 (± 0.004 over 3 runs), vs 0.896 for a keyword baseline
• 30 daily runs, 256 postings, 127 with full job-description text
• Tailoring raised requirement coverage from 40.1% to 44.2%. That's modest, and I report it as measured.

It runs as a serverless pipeline on AWS (Step Functions, Lambda, DynamoDB, S3, CloudFront, defined in CDK and checked with cdk-nag) and locally from the same code. It's not deployed yet, so the cost figure in the README is an estimate.

Repo: <link>

#softwareengineering #aws #llm #internships

---

**Word count:** 176 words (post body from "I built" to before "Repo:"), counted with `wc -w`.

## Suggested images (from the repo)

1. `docs/screenshot.png`: the dashboard hero (KPI tiles) with demand bars and the 7-day rolling chart
   (dark-mode variant: `docs/images/dashboard-dark.png`; phone: `docs/images/mobile-light.png`).
2. `docs/architecture.png`: the architecture diagram (light theme).
3. `docs/images/timelines.png`: run history + project roadmap timelines (or `docs/images/resume-example.png`,
   a tailored one-page resume for the fictional demo persona).
