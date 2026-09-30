/**
 * API utility for Colons web client
 */
import axios from 'axios';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

export const apiService = {
  // Chat endpoints
  chat: (message: string, stream: boolean = false) =>
    api.post('/chat', { message, stream }),

  // Task endpoints
  createTask: (description: string) =>
    api.post('/tasks', { description }),
  listTasks: (status?: string) =>
    api.get('/tasks', { params: { status } }),
  getTask: (taskId: string) =>
    api.get(`/tasks/${taskId}`),
  executeTask: (taskId: string) =>
    api.post(`/tasks/${taskId}/execute`),

  // Memory endpoints
  searchMemory: (query: string, limit: number = 5) =>
    api.post('/memory/search', { query, limit }),
  getHistory: (limit: number = 20, offset: number = 0) =>
    api.get('/memory/history', { params: { limit, offset } }),
  storeMemory: (content: string, metadata?: object) =>
    api.post('/memory/store', { content, metadata }),
  clearMemory: (memoryId?: number) =>
    api.delete('/memory', { params: { memory_id: memoryId } }),

  // Usage endpoint
  getUsage: () =>
    api.get('/usage'),

  // Agent endpoints
  getAgentStatus: () =>
    api.get('/agent/status'),
  updateAgentConfig: (config: object) =>
    api.post('/agent/config', config),
  resetAgent: () =>
    api.post('/agent/reset'),

  // Provider endpoints
  listProviders: () =>
    api.get('/providers'),
  switchProvider: (config: object) =>
    api.post('/provider/switch', config),

  // Cache endpoints
  clearCache: (pattern: string = 'colons:*') =>
    api.post('/cache/clear', { pattern }),

  // Health check
  healthCheck: () =>
    api.get('/health'),
};

export default apiService;