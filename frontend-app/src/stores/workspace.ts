import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { asList } from '../api/client'
import { listWorkspaces } from '../api/settings'
import { useAuthStore } from './auth'

export const useWorkspaceStore = defineStore('workspace', () => {
  const workspaces = ref<Record<string, any>[]>([])
  const loading = ref(false)
  const currentWorkspace = computed(() => {
    const auth = useAuthStore()
    return workspaces.value.find((w) => String(w.id) === String(auth.workspaceId)) || workspaces.value[0] || null
  })

  async function load() {
    loading.value = true
    try {
      const auth = useAuthStore()
      workspaces.value = asList(await listWorkspaces(), ['workspaces', 'items'])
      if (!auth.workspaceId && workspaces.value[0]?.id) auth.setWorkspace(String(workspaces.value[0].id))
      return workspaces.value
    } finally {
      loading.value = false
    }
  }

  return { workspaces, loading, currentWorkspace, load }
})
