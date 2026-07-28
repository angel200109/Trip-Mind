<script setup lang="ts">
import { storeToRefs } from "pinia";
import { chatbotMessage } from "@/store";
import ChatPane from "./components/ChatPane.vue";
import ChatInputBar from "./components/ChatInputBar.vue";
import ConversationSidebar from "./components/ConversationSidebar.vue";
import { onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

const store = chatbotMessage();
const { conversations, currentConversationId } = storeToRefs(store);
const route = useRoute();
const router = useRouter();

const handleCreateConversation = () => {
  store.startNewConversation();
  router.push("/");
};

const handleSelectConversation = (id: string) => {
  router.push(`/chat/${id}`);
};

onMounted(async () => {
  await store.loadConversations();
});

watch(
  () => route.params.id,
  async (id) => {
    await store.syncConversationByRoute(typeof id === "string" ? id : undefined);
  },
  { immediate: true }
);
</script>

<template>
  <div class="home">
    <ConversationSidebar
      :conversations="conversations"
      :active-id="currentConversationId"
      @create="handleCreateConversation"
      @select="handleSelectConversation"
    />
    <div class="chat-main">
      <ChatPane />
      <ChatInputBar />
    </div>
  </div>
</template>

<style scoped>
.home {
  height: 100vh;
  display: flex;
  flex-direction: row;
  overflow: hidden;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
</style>
