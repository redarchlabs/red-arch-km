/**
 * Public, logged-out marketing/informational content — the "what is this app"
 * surface shown to visitors before they sign in. This is the single source of
 * truth for both the landing-page feature grid (see
 * ../landing/landingContent.ts) and the public `/help/*` pages.
 *
 * Keep every card grounded in a feature km-2 actually ships today. Features that
 * are planned but not built are marked `comingSoon` — they render with a badge
 * and their deep page carries a "coming soon" note instead of implying the
 * capability exists. When km-2 gains a feature, flip `comingSoon` off (or add a
 * new entry); when copy here would advertise something unbuilt, don't add it.
 */
import {
  Boxes,
  ClipboardList,
  FileText,
  FolderTree,
  GraduationCap,
  MessagesSquare,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";

export interface HelpCard {
  title: string;
  description: string;
  /** Marks a sub-capability that isn't built yet. */
  comingSoon?: boolean;
}

export interface PublicFeature {
  /** URL slug under /help/<slug> and stable key. */
  slug: string;
  /** Short name shown on cards and nav. */
  title: string;
  /** One-line tagline under the title. */
  tagline: string;
  /** Intro paragraph for the deep page and the landing card blurb. */
  intro: string;
  icon: LucideIcon;
  /** Whole feature is planned, not shipped — badge + note, no live claim. */
  comingSoon?: boolean;
  /** Detail cards for the deep /help/<slug> page. */
  cards: HelpCard[];
}

export const PUBLIC_FEATURES: PublicFeature[] = [
  {
    slug: "documents",
    title: "Universal Documents",
    tagline: "The core building block of your knowledge system — rich, secure, searchable.",
    intro:
      "Upload PDFs, Office files, images, plain text, and Markdown — km-2 extracts the text and section summaries so every document becomes searchable and answerable. Documents keep their original formatting, and very large files load a section at a time so the reader stays fast.",
    icon: FileText,
    cards: [
      {
        title: "Any file type",
        description:
          "Automatically extract text and insights from PDFs, images, Office files, and more — you don't have to convert anything first.",
      },
      {
        title: "Faithful reader",
        description:
          "Markdown, .docx, and text render with headings, lists, and tables; PDFs and images show extracted text alongside the untouched original.",
      },
      {
        title: "Section summaries",
        description:
          "Each document is summarized section by section, shown side-by-side or inline with the full text and scroll-synced as you read.",
      },
      {
        title: "AI vectorization",
        description:
          "Every document is embedded for semantic search and grounds the assistant's answers — with citations back to the source.",
      },
    ],
  },
  {
    slug: "folders",
    title: "Resources & Folders",
    tagline: "Organize your knowledge hierarchically for filtering, permissions, and relevance.",
    intro:
      "Resources is your knowledge base, organized as a folder tree. Browse and search across everything you're allowed to see, upload straight into a folder, and let documents inherit that folder's access rules — overriding per document when you need to.",
    icon: FolderTree,
    cards: [
      {
        title: "Hierarchical structure",
        description:
          "A familiar folder tree on the left, the selected folder's contents on the right — sortable by name, type, modified, or size.",
      },
      {
        title: "Upload in place",
        description:
          "Add documents straight into a folder; they inherit that folder's viewer and contributor rules at upload time.",
      },
      {
        title: "Fast at scale",
        description:
          "The tree and contents are virtualized, so browsing stays responsive with thousands of folders and documents.",
      },
    ],
  },
  {
    slug: "security",
    title: "Fine-Grained Security",
    tagline: "Protecting your data at every layer — search, chat, and retrieval.",
    intro:
      "Ensure the right people access the right information. Permissions are built from your organization's dimensions — regions, departments, roles, and groups — and enforced everywhere, including search and AI chat, so answers only ever draw on what a user is authorized to read.",
    icon: ShieldCheck,
    cards: [
      {
        title: "Dimension-based access",
        description:
          "Grant access by region, department, role, and group, alone or in combination, to match how your organization is actually structured.",
      },
      {
        title: "Permission-aware AI",
        description:
          "Search and chat are scoped to each user — results and answers never surface content the viewer isn't allowed to see.",
      },
      {
        title: "Document-level overrides",
        description:
          "A document inherits its folder's rules by default, and you can tighten or widen access for that single document in its Properties.",
      },
    ],
  },
  {
    slug: "chat",
    title: "AI-Powered Chat",
    tagline: "Ask questions and get grounded, permission-aware answers from your knowledge base.",
    intro:
      "Chat with your knowledge base in natural language. Answers are grounded only in your organization's documents — the assistant retrieves relevant passages and answers from them, cites its sources inline, and says so when it can't find anything relevant rather than guessing.",
    icon: MessagesSquare,
    cards: [
      {
        title: "Grounded in your documents",
        description:
          "Retrieval-augmented answers draw from your content, not general knowledge — and cite each claim with inline [n] markers back to the source.",
      },
      {
        title: "Scoped conversations",
        description:
          "Limit an answer to specific folders with the Scope selector, and keep asking follow-ups — the conversation holds context.",
      },
      {
        title: "Permission-aware",
        description:
          "Chat only ever reads documents your permissions allow, so shared assistants stay safe across teams.",
      },
    ],
  },
  {
    slug: "entities",
    title: "Entities & Structured Data",
    tagline: "Track the people, accounts, and records your documents are about.",
    intro:
      "Beyond free-form documents, km-2 lets you model the structured records your organization cares about — and connect them to the documents and forms that reference them.",
    icon: Boxes,
    cards: [
      {
        title: "Structured records",
        description:
          "Define entity types and store the fields that matter, kept alongside the documents that describe them.",
      },
      {
        title: "Connected knowledge",
        description:
          "Link entities to documents and intake forms so context travels with the record.",
      },
    ],
  },
  {
    slug: "forms",
    title: "Forms & Intake",
    tagline: "Collect information from anyone with a secure, token-linked form.",
    intro:
      "Send a public, token-linked intake form to someone outside your organization and capture their response straight into a record — no account required for the person filling it out.",
    icon: ClipboardList,
    cards: [
      {
        title: "Public token links",
        description:
          "Share a one-time form link; the recipient completes it without signing in, and the response flows back into your data.",
      },
      {
        title: "Updates your records",
        description:
          "Form submissions update the linked entity, whether it's a single inline record or one of many rows.",
      },
    ],
  },
  {
    slug: "workflows",
    title: "Workflows & Automation",
    tagline: "Automate the routine so your team can focus on the work.",
    intro:
      "Build workflows that react to what happens in your knowledge base — sending a form, notifying a person, or advancing a record — so repetitive steps run themselves.",
    icon: Workflow,
    cards: [
      {
        title: "Trigger-driven",
        description:
          "Publish a workflow and run it on demand or in response to events, with per-workflow permission controls over who can run it.",
      },
      {
        title: "Actions that fit",
        description:
          "Send forms, notify people, and move records forward — composed into the sequence your process needs.",
      },
    ],
  },
  {
    slug: "training",
    title: "Training & Simulation",
    tagline: "Hands-on learning experiences grounded in your SOPs — coming soon.",
    intro:
      "Deliver immersive, AI-driven training scenarios grounded in your own documents and evaluated with rubrics. This capability is on the roadmap and not yet available in km-2.",
    icon: GraduationCap,
    comingSoon: true,
    cards: [
      {
        title: "Outcome-based learning",
        description:
          "Practice against realistic, LLM-driven scenarios built from your SOPs and knowledge base.",
        comingSoon: true,
      },
      {
        title: "Rubric-based evaluation",
        description:
          "AI coach and grader roles assess performance against rubrics and track progress over time.",
        comingSoon: true,
      },
    ],
  },
];

/** Look up one feature by its slug (for /help/[slug]). */
export function featureBySlug(slug: string): PublicFeature | undefined {
  return PUBLIC_FEATURES.find((f) => f.slug === slug);
}
