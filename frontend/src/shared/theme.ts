// Original frontend Ant Design theme, MIT licensed.
export function initializeLightTheme(): void {
  document.documentElement.classList.remove('dark')
  document.documentElement.style.colorScheme = 'light'
  try {
    localStorage.removeItem('zhiheng-color-scheme')
  } catch {
    // Restricted storage must not prevent the fixed light interface from loading.
  }
}
export const componentSize = 'middle'

const sharedComponents = {
  Button: {
    borderRadius: 6,
    controlHeight: 36,
    fontWeight: 400
  },
  Card: {
    borderRadiusLG: 0,
    colorBorderSecondary: 'transparent',
    boxShadowTertiary: 'none'
  },
  Drawer: {
    paddingLG: 20
  },
  Input: {
    borderRadius: 6,
    controlHeight: 36
  },
  Menu: {
    borderRadius: 6,
    controlHeight: 34
  },
  Table: {
    borderColor: '#ECEEF3',
    headerBg: '#FFFFFF',
    headerColor: '#7A7F8E',
    rowHoverBg: '#F8F7FF'
  }
}

export const lightTheme = {
  token: {
    colorPrimary: '#6155EE',
    colorLink: '#6155EE',
    colorLinkHover: '#5145D8',
    colorInfo: '#1890FF',
    colorSuccess: '#52C41A',
    colorWarning: '#FAAD14',
    colorError: '#FF4D4F',
    colorBgLayout: '#FFFFFF',
    colorBgContainer: '#FFFFFF',
    colorBgElevated: '#FFFFFF',
    colorBorder: '#ECEEF3',
    colorBorderSecondary: '#ECEEF3',
    colorText: '#242733',
    colorTextSecondary: '#7A7F8E',
    colorTextTertiary: '#969CAB',
    borderRadius: 6,
    borderRadiusLG: 8,
    controlHeight: 36,
    fontFamily: 'var(--workspace-font-family)',
    fontFamilyCode: 'var(--workspace-font-mono)',
    fontSize: 14,
    lineHeight: 1.5
  },
  components: sharedComponents
}
