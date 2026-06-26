import { HelpCircle } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface Props {
  text: string;
  label?: string;
}

export function HelpTip({ text, label = 'Help' }: Props) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={label}
        >
          <HelpCircle size={11} />
        </button>
      </TooltipTrigger>
      <TooltipContent sideOffset={4} className="max-w-[240px] text-left">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}
