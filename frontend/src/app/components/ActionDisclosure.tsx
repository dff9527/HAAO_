interface Props {
  text: string;
}

export function ActionDisclosure({ text }: Props) {
  return <p className="text-[11px] text-muted-foreground leading-snug">{text}</p>;
}
