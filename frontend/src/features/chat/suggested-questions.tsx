import { ArrowRight, Building2, Landmark, Repeat2, Scale } from "lucide-react";

export const SUGGESTIONS = [
  {
    icon: Repeat2,
    question: "How does a 1031 exchange work?",
    description: "Like-kind rules, deadlines and qualified intermediaries",
  },
  {
    icon: Landmark,
    question: "What is a Delaware Statutory Trust?",
    description: "Structure, IRS treatment and use as replacement property",
  },
  {
    icon: Building2,
    question: "How does a 721 exchange differ from a 1031 exchange?",
    description: "Contributing property to a REIT operating partnership",
  },
  {
    icon: Scale,
    question: "What are important DST considerations?",
    description: "Liquidity, control, fees and other risks to understand",
  },
];

export function SuggestedQuestions({
  onSelect,
  disabled,
}: {
  onSelect: (question: string) => void;
  disabled: boolean;
}) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2" aria-label="Suggested questions">
      {SUGGESTIONS.map(({ icon: Icon, question, description }) => (
        <li key={question}>
          <button
            type="button"
            onClick={() => onSelect(question)}
            disabled={disabled}
            className="group bg-card hover:border-sand-strong hover:bg-sand/25 flex h-full w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors disabled:opacity-60"
          >
            <span className="bg-secondary flex size-9 shrink-0 items-center justify-center rounded-lg">
              <Icon className="size-4.5" aria-hidden />
            </span>
            <span className="min-w-0 flex-1 space-y-1">
              <span className="block text-sm leading-snug font-semibold">{question}</span>
              <span className="text-muted-foreground block text-xs leading-relaxed">
                {description}
              </span>
            </span>
            <ArrowRight
              className="text-muted-foreground mt-1 size-4 shrink-0 transition-transform group-hover:translate-x-0.5"
              aria-hidden
            />
          </button>
        </li>
      ))}
    </ul>
  );
}
