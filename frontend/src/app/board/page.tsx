import type { Metadata } from "next";
import { BoardView } from "@/components/board/BoardView";

export const metadata: Metadata = { title: "Lead board" };

export default function BoardPage() {
  return <BoardView />;
}
