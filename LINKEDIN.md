# LinkedIn post draft

> Draft only. Review before posting; every number links back to a file in the repo.

---

I built jobpulse, an open-source agent that reads the internship market for me every morning.

Live dashboard: https://d203ftbw510zrj.cloudfront.net
Code: https://github.com/xungirl/jobpulse

Every day at 6 a.m. it pulls new Summer 2027 SWE/AI postings, uses an LLM to extract what each job asks for, tracks which skills are rising, tailors a one-page resume per posting, and suggests portfolio projects for my skill gaps.

One rule I wouldn't bend: the tailor only selects and reorders bullets I already wrote. It never writes new claims. A property test checked 2,000 generated resumes with 0 violations.

What I measured:
• Skill extraction F1 0.946 (± 0.004 over 3 runs) on 30 labeled job descriptions, vs 0.896 for keyword matching
• A 30-day backfill over 256 postings, 30/30 runs green
• Resume requirement coverage 40.1% → 44.2%: modest, and reported as measured

It runs serverless on AWS (Step Functions, Lambda, DynamoDB, S3, CloudFront, all defined in CDK), at an estimated cent or so a month.

My favorite bug: every test passed, but the first real run failed. Each function read the API key at startup, while least-privilege IAM only allowed two of them to. Mocks don't enforce IAM; production does.

Built with Claude Code as my pair programmer.

#AWS #LLM #Serverless #AIAgents #Internships

---

**Word count:** 207 words (whole post incl. links and hashtags), counted with str.split().

## Suggested images (from the repo)

1. `docs/screenshot.png`: the dashboard hero (KPI tiles) with demand bars and the 7-day rolling chart
   (dark-mode variant: `docs/images/dashboard-dark.png`; phone: `docs/images/mobile-light.png`).
2. `docs/architecture.png`: the architecture diagram (light theme).
3. `docs/images/timelines.png`: run history + project roadmap timelines (or `docs/images/resume-example.png`,
   a tailored one-page resume for the fictional demo persona).
