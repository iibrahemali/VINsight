import { Compass, GraduationCap, Headset, ScanSearch } from "lucide-react";

import type { PersonaId } from "@/lib/agent";

const ICONS = { inspector: ScanSearch, scout: Compass, coach: GraduationCap, advisor: Headset } as const;

export function PersonaIcon({ id, size = 16, className }: { id: PersonaId; size?: number; className?: string }) {
  const Icon = ICONS[id];
  return <Icon size={size} className={className} />;
}
