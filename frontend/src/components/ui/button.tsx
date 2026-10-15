import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap border font-mono text-[11px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-primary bg-hud-ghost text-primary hover:bg-primary hover:text-primary-foreground",
        destructive: "border-hud-red/70 bg-hud-red/10 text-hud-red hover:bg-hud-red/25",
        outline: "border-border bg-background text-foreground hover:border-primary hover:text-primary",
        secondary: "border-border bg-secondary text-secondary-foreground hover:border-primary hover:text-primary",
        ghost: "border-transparent text-muted-foreground hover:bg-hud-ghost hover:text-primary",
        link: "border-transparent text-primary underline-offset-4 hover:underline",
        success: "border-hud-green/70 bg-hud-green/10 text-hud-green hover:bg-hud-green/25",
        warning: "border-hud-amber/70 bg-hud-amber/10 text-hud-amber hover:bg-hud-amber/25",
      },
      size: {
        default: "h-8 px-3",
        sm: "h-7 px-2.5 text-[10px]",
        lg: "h-10 px-5 text-xs",
        icon: "h-8 w-8",
        "icon-sm": "h-7 w-7",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, loading = false, children, disabled, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} disabled={disabled || loading} {...props}>
      {loading ? <Loader2 className="animate-spin" /> : null}
      {children}
    </Comp>
  );
});
Button.displayName = "Button";

export { Button, buttonVariants };
