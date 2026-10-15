"""Source material for the generated task suite: people, topics, pages, meetings, and deals."""

# ============= People =============
FIRST_NAMES = [
    "Maya",
    "Omar",
    "Lena",
    "Ravi",
    "Sofia",
    "Daniel",
    "Aisha",
    "Tomas",
    "Priya",
    "Jonah",
    "Elena",
    "Kenji",
    "Nadia",
    "Marcus",
    "Ingrid",
    "Felix",
    "Hana",
    "Diego",
    "Chloe",
    "Samir",
]
LAST_NAMES = [
    "Chen",
    "Haddad",
    "Novak",
    "Iyer",
    "Rossi",
    "Okafor",
    "Khan",
    "Silva",
    "Nair",
    "Becker",
    "Morales",
    "Tanaka",
    "Petrov",
    "Webb",
    "Larsen",
    "Brandt",
    "Kimura",
    "Ortega",
    "Dubois",
    "Aziz",
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# ============= Slack Digest Topics =============
# each topic: subject, two (question, rejected option, decision keyword) triples, three tasks
DIGEST_TOPICS = [
    {
        "topic": "the billing database migration",
        "decisions": [
            ("the target database", "MySQL 8", "Postgres 16"),
            ("the cutover window", "a weekday evening", "Saturday 02:00 UTC"),
        ],
        "tasks": ["write the rollback runbook", "run the dry-run migration", "notify finance"],
    },
    {
        "topic": "the mobile app 4.2 release",
        "decisions": [
            ("the release train", "a big-bang launch", "a staged rollout"),
            ("the crash threshold", "1 percent", "the crash-free gate"),
        ],
        "tasks": ["freeze the release branch", "update the store listing", "brief support"],
    },
    {
        "topic": "the on-call rotation",
        "decisions": [
            ("the rotation length", "two weeks", "one-week shifts"),
            ("the paging tool", "email alerts", "PagerDuty escalation"),
        ],
        "tasks": ["publish the new schedule", "configure escalation policies", "run a fire drill"],
    },
    {
        "topic": "the Q3 pricing change",
        "decisions": [
            ("the starter tier price", "19 dollars", "the Starter Plus tier"),
            ("grandfathering", "no grandfathering", "a grandfather period"),
        ],
        "tasks": [
            "update the pricing page",
            "email existing customers",
            "change the billing plans",
        ],
    },
    {
        "topic": "the observability vendor",
        "decisions": [
            ("the tracing backend", "a self-built stack", "Grafana Tempo"),
            ("log retention", "90 days", "30-day log retention"),
        ],
        "tasks": [
            "sign the vendor contract",
            "migrate the dashboards",
            "decommission the old agent",
        ],
    },
    {
        "topic": "the design system refresh",
        "decisions": [
            ("the component library", "a fork of the old kit", "Radix primitives"),
            ("the color tokens", "the legacy palette", "the new token set v2"),
        ],
        "tasks": [
            "audit existing components",
            "write the migration guide",
            "update Figma libraries",
        ],
    },
    {
        "topic": "the customer onboarding flow",
        "decisions": [
            ("the signup step count", "five steps", "a three-step signup"),
            ("email verification", "verify first", "deferred email verification"),
        ],
        "tasks": ["ship the new signup form", "add funnel analytics", "rewrite the welcome email"],
    },
    {
        "topic": "the data warehouse costs",
        "decisions": [
            ("the compute plan", "on-demand only", "reserved capacity"),
            ("stale tables", "keep everything", "a 180-day table expiry"),
        ],
        "tasks": ["tag tables by owner", "cancel unused pipelines", "report monthly spend"],
    },
    {
        "topic": "the API versioning policy",
        "decisions": [
            ("the version scheme", "URL path versions", "date-based API versions"),
            ("the deprecation window", "3 months", "a 12-month deprecation window"),
        ],
        "tasks": ["draft the policy doc", "add version headers", "email partner developers"],
    },
    {
        "topic": "the security audit findings",
        "decisions": [
            ("the SSO requirement", "optional SSO", "mandatory SSO for admins"),
            ("secret storage", "environment files", "HashiCorp Vault"),
        ],
        "tasks": ["rotate the leaked keys", "enable SSO enforcement", "file the audit response"],
    },
    {
        "topic": "the hiring plan for platform",
        "decisions": [
            ("the first hire", "a manager first", "two senior SREs"),
            ("the interview loop", "five rounds", "a four-round loop"),
        ],
        "tasks": ["post the job descriptions", "train new interviewers", "book the recruiter sync"],
    },
    {
        "topic": "the support ticket backlog",
        "decisions": [
            ("triage ownership", "a shared queue", "a weekly triage captain"),
            ("the response target", "48 hours", "a 24-hour first-response target"),
        ],
        "tasks": [
            "close duplicate tickets",
            "set up the triage dashboard",
            "update the help center",
        ],
    },
    {
        "topic": "the offsite planning",
        "decisions": [
            ("the location", "a city hotel", "the Lisbon venue"),
            ("the agenda format", "all talks", "half-day workshops"),
        ],
        "tasks": ["book the flights", "send the agenda survey", "confirm the catering"],
    },
    {
        "topic": "the search relevance project",
        "decisions": [
            ("the ranking model", "keyword boosts", "the hybrid reranker"),
            ("the evaluation set", "ad hoc queries", "a 500-query judged set"),
        ],
        "tasks": [
            "label the judged queries",
            "ship the reranker behind a flag",
            "write the results memo",
        ],
    },
    {
        "topic": "the laptop refresh",
        "decisions": [
            ("the standard model", "15-inch laptops", "14-inch M4 laptops"),
            ("device management", "manual setup", "Kandji enrollment"),
        ],
        "tasks": ["order the laptops", "prepare the setup guide", "collect the old devices"],
    },
]

# ============= Notion Knowledge Base =============
KB_TAGS = ["security", "onboarding", "billing", "infrastructure", "hiring", "legal"]
KB_PAGES = [
    (
        "Password Rotation Policy",
        "security",
        "Service passwords rotate every 90 days through Vault.",
    ),
    (
        "Incident Disclosure Process",
        "security",
        "Customer-facing incidents are disclosed within 72 hours.",
    ),
    (
        "Laptop Encryption Standard",
        "security",
        "All laptops use full-disk encryption enforced by MDM.",
    ),
    (
        "Access Review Checklist",
        "security",
        "Admin access is reviewed quarterly by each team lead.",
    ),
    ("New Hire First Week", "onboarding", "Day-by-day schedule for a new engineer's first week."),
    ("Buddy Program Guide", "onboarding", "Every new hire is paired with a buddy for 30 days."),
    (
        "Dev Environment Setup",
        "onboarding",
        "Steps to clone the monorepo and run services locally.",
    ),
    ("Refund Approval Rules", "billing", "Refunds above 500 USD need finance approval."),
    ("Invoice Schedule", "billing", "Invoices are issued on the first business day of each month."),
    (
        "Dunning Email Sequence",
        "billing",
        "Failed payments trigger three reminder emails over 14 days.",
    ),
    ("Tax Exemption Handling", "billing", "Exempt customers upload certificates before invoicing."),
    (
        "Kubernetes Upgrade Runbook",
        "infrastructure",
        "Clusters upgrade one minor version per quarter.",
    ),
    (
        "Backup Retention Matrix",
        "infrastructure",
        "Database backups are kept 35 days, snapshots 7 days.",
    ),
    (
        "Region Failover Plan",
        "infrastructure",
        "Traffic fails over to the secondary region within 15 minutes.",
    ),
    ("Interview Scorecard Rubric", "hiring", "Candidates are scored 1 to 4 on four competencies."),
    ("Referral Bonus Policy", "hiring", "Referral bonuses pay out after the hire's 90th day."),
    ("Offer Approval Workflow", "hiring", "Offers above band need VP and finance sign-off."),
    ("Data Processing Agreement", "legal", "The standard DPA covers GDPR and CCPA obligations."),
    (
        "Contract Signature Authority",
        "legal",
        "Only directors may sign contracts above 50,000 USD.",
    ),
    ("Open Source License Policy", "legal", "Copyleft licenses need legal review before use."),
]
KB_QUESTIONS = {
    "security": [
        "What are our security policies for credentials and devices?",
        "Where can I find the security rules I need to follow?",
        "Which pages describe how we handle security?",
    ],
    "onboarding": [
        "I'm starting next week, what onboarding docs should I read?",
        "Which pages cover onboarding for new engineers?",
        "What do we have documented about onboarding new hires?",
    ],
    "billing": [
        "Where are the billing rules documented?",
        "A customer asked about billing, which pages explain our process?",
        "What billing policies do we have written down?",
    ],
    "infrastructure": [
        "Which infrastructure runbooks exist?",
        "Where is our infrastructure documentation for outages and upgrades?",
        "What infrastructure pages should on-call know about?",
    ],
    "hiring": [
        "What hiring policies are documented?",
        "Which pages explain how we run hiring?",
        "I'm a new hiring manager, which hiring docs apply to me?",
    ],
    "legal": [
        "Which legal policies should I check before signing anything?",
        "Where do we document legal requirements?",
        "What legal guidance do we have in the knowledge base?",
    ],
}

# ============= Meetings =============
MEETING_TITLES = [
    "Platform Weekly Sync",
    "Mobile Release Review",
    "Pricing Committee",
    "Security Council",
    "Design Critique",
    "Growth Experiments Review",
    "Infrastructure Planning",
    "Support Escalations Review",
    "Hiring Committee",
    "Quarterly Business Review",
    "Data Platform Roadmap",
    "Partner Integrations Sync",
    "Incident Postmortem",
    "Customer Advisory Prep",
    "Launch Readiness Check",
]
MEETING_DECISIONS = [
    "Move the launch to the second week of the month",
    "Adopt the new on-call handbook",
    "Pause the referral experiment",
    "Fund the observability upgrade",
    "Keep the current pricing for enterprise",
    "Require design review for every new screen",
    "Retire the legacy export endpoint",
    "Run the beta with five design partners",
]
MEETING_TASKS = [
    "send the recap to stakeholders",
    "update the roadmap slide",
    "file the vendor security review",
    "draft the customer announcement",
    "schedule the follow-up session",
    "fix the flaky integration test",
    "prepare the budget estimate",
    "collect feedback from support",
]

# ============= CRM =============
COMPANY_NAMES = [
    "Acme Robotics",
    "Globex Logistics",
    "Initech Health",
    "Umbrella Retail",
    "Stark Analytics",
    "Wayne Foods",
    "Hooli Media",
    "Vandelay Imports",
    "Soylent Labs",
    "Cyberdyne Energy",
    "Tyrell Mobility",
    "Wonka Brands",
    "Gringotts Finance",
    "Oscorp Materials",
    "Pied Piper Cloud",
]
DEAL_KINDS = [
    "Platform Expansion",
    "Annual Renewal",
    "Pilot Program",
    "Enterprise License",
    "Analytics Add-on",
    "Support Upgrade",
    "Multi-region Rollout",
    "Seat Expansion",
]
OPEN_STAGES = ["NEW", "SCREENING", "MEETING", "PROPOSAL"]
ALL_STAGES = OPEN_STAGES + ["CUSTOMER"]
