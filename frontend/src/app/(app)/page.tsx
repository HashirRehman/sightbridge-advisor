import { Suspense } from "react";

import { ChatView } from "@/features/chat/chat-view";

export default function AssistantPage() {
  return (
    <Suspense>
      <ChatView />
    </Suspense>
  );
}
