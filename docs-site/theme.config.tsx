import React from 'react'
import { DocsThemeConfig } from 'nextra-theme-docs'

const config: DocsThemeConfig = {
  logo: (
    <span style={{ fontWeight: 700, fontSize: '1.125rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M2 17L12 22L22 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M2 12L12 17L22 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      Case Automation MCP
    </span>
  ),
  project: {
    link: 'https://github.com/pt-act/Case_Automation_MCP',
  },
  chat: {
    link: 'https://github.com/pt-act/Case_Automation_MCP/issues',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2C6.48 2 2 6.48 2 12c0 1.54.36 3 .97 4.29L2 22l5.71-.97C9 21.64 10.46 22 12 22c5.52 0 10-4.48 10-10S17.52 2 12 2zm0 18c-1.37 0-2.67-.38-3.81-1.05l-.27-.16-3.42.58.58-3.42-.16-.27C4.38 14.67 4 13.37 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8-3.59 8-8 8zm4.59-6.59c-.25-.12-1.47-.72-1.69-.81-.23-.08-.39-.12-.56.12-.17.25-.64.81-.78.97-.14.17-.29.19-.54.06-.25-.12-1.05-.39-1.99-1.23-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.02-.38.11-.51.11-.11.25-.29.37-.43.12-.14.17-.25.25-.42.08-.17.04-.31-.02-.43-.06-.12-.56-1.34-.76-1.84-.2-.48-.4-.42-.56-.43-.14 0-.31-.01-.48-.01-.17 0-.43.06-.66.31-.23.25-.86.84-.86 2.05 0 1.21.88 2.38 1 2.54.12.17 1.73 2.64 4.19 3.7.59.25 1.05.4 1.41.51.59.19 1.13.16 1.56.1.48-.07 1.47-.6 1.68-1.18.21-.58.21-1.07.14-1.18-.06-.11-.23-.17-.48-.29z"/>
      </svg>
    ),
  },
  docsRepositoryBase: 'https://github.com/pt-act/Case_Automation_MCP/tree/main/docs-site',
  footer: {
    text: (
      <span>
        Case Automation MCP Server — MIT License
      </span>
    ),
  },
  search: {
    placeholder: 'Search documentation...',
  },
  head: (
    <>
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <meta name="description" content="Case Automation MCP Server — AI-orchestrated workflow automation for legal practice" />
      <meta name="theme-color" content="#7c3aed" />
    </>
  ),
  useNextSeoProps() {
    return {
      titleTemplate: '%s – Case Automation MCP',
    }
  },
  banner: {
    key: 'v1.0-release',
    text: (
      <a href="https://github.com/pt-act/Case_Automation_MCP" target="_blank" rel="noreferrer">
        Case Automation MCP v1.0 — Read the release notes →
      </a>
    ),
  },
}

export default config
