/**
 * Main App component - Colons web client
 */
import React from 'react';
import { ChatPage } from './components/ChatPage';
import './styles/globals.css';

export const App: React.FC = () => {
  return <ChatPage />;
};

export default App;