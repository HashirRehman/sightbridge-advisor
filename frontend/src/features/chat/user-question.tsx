export function UserQuestion({ text, label = "You asked" }: { text: string; label?: string }) {
  return (
    <div className="flex justify-end">
      <p className="bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-[0.95rem] leading-relaxed break-words">
        <span className="sr-only">{label}: </span>
        {text}
      </p>
    </div>
  );
}
