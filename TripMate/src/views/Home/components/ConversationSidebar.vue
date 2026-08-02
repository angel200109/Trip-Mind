<script setup lang="ts">
import { computed } from "vue";
import { Plus, More } from "@element-plus/icons-vue";
import avatarImage from "@/assets/avatar.png";
import type { Conversation } from "@/types";

const props = withDefaults(
  defineProps<{
    activeId: string;
    conversations?: Conversation[];
  }>(),
  {
    conversations: () => [],
  }
);

const emit = defineEmits<{
  (e: "create"): void;
  (e: "select", id: string): void;
}>();

const sessionGroups = computed(() => {
  const groups = new Map<string, Conversation[]>();
  const conversations = Array.isArray(props.conversations)
    ? props.conversations
    : [];

  console.log("[DEBUG] ConversationSidebar props.conversations:", conversations);

  conversations.forEach((conversation) => {
    const items = groups.get(conversation.groupLabel) ?? [];
    items.push(conversation);
    groups.set(conversation.groupLabel, items);
  });

  const result = Array.from(groups, ([label, items]) => ({ label, items }));
  console.log("[DEBUG] sessionGroups 分组结果:", result);
  return result;
});
</script>

<template>
  <aside class="conversation-sidebar">
    <div class="sidebar-header">
      <div class="brand">TripMate</div>
    </div>

    <button class="new-chat-btn" type="button" @click="emit('create')">
      <el-icon><Plus /></el-icon>
      <span>开启新对话</span>
    </button>

    <div class="session-groups">
      <section
        v-for="group in sessionGroups"
        :key="group.label"
        class="session-group"
      >
        <h3 class="group-title">{{ group.label }}</h3>
        <div class="group-list">
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            class="session-item"
            :class="{ active: item.id === props.activeId }"
            @click="emit('select', item.id)"
          >
            <div class="session-main">
              <div class="session-title">{{ item.title }}</div>
            </div>
            <el-icon class="session-more"><More /></el-icon>
          </button>
        </div>
      </section>
    </div>

    <div class="sidebar-footer">
      <img class="avatar" :src="avatarImage" alt="avatar" />
      <div class="user-name">你好杰妮</div>
    </div>
  </aside>
</template>

<style scoped lang="less">
.conversation-sidebar {
  width: 260px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #f7f8fb 0%, #f3f5f9 100%);
  color: #20242d;
  padding: 16px 12px 12px;
  box-sizing: border-box;
  border-right: 1px solid #e8ebf2;
  flex-shrink: 0;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding: 0 8px;
}

.brand {
  font-size: 18px;
  line-height: 1;
  font-weight: 700;
  color: #4e6bff;
  letter-spacing: -0.3px;
}

.new-chat-btn {
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 8px;
  background: linear-gradient(180deg, #ffffff 0%, #fbfcfe 100%);
  box-shadow:
    inset 0 0 0 1px #e8ebf2,
    0 1px 2px rgba(15, 23, 42, 0.03);
  color: #434b5c;
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 16px;
  border: none;
  cursor: pointer;
  transition: background 0.2s;

  &:hover {
    background: #ffffff;
    box-shadow:
      inset 0 0 0 1px #d0d5e0,
      0 2px 4px rgba(15, 23, 42, 0.06);
  }
}

.session-groups {
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
}

.session-group + .session-group {
  margin-top: 16px;
}

.group-title {
  margin: 0 0 8px;
  padding: 0 8px;
  font-size: 12px;
  font-weight: 500;
  color: #9ca3af;
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  background: transparent;
  border-radius: 8px;
  padding: 10px 10px;
  color: #30384a;
  border: none;
  cursor: pointer;
  font: inherit;
  transition: background 0.15s;

  &:hover {
    background: #ebeef5;
  }
}

.session-item.active {
  background: #dde8ff;
  color: #4e6bff;
}

.session-main {
  min-width: 0;
  flex: 1;
}

.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
}

.session-more {
  flex-shrink: 0;
  color: #98a1b3;
  font-size: 16px;
}

.sidebar-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 48px;
  margin-top: 12px;
  padding: 0 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.68);
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
}

.user-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
