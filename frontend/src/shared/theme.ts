// Original frontend Ant Design theme, MIT licensed.
import { useDark } from '@vueuse/core'
export const isDark = useDark({ storageKey: 'zhiheng-color-scheme' })
export function toggleTheme(): void {
  isDark.value = !isDark.value
}
export const componentSize = 'middle'

const sharedComponents = {
  Button: {
    borderRadius: 6,
    controlHeight: 36,
    fontWeight: 400
  },
  Card: {
    borderRadiusLG: 8,
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
    headerBg: '#FAFBFE',
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
    colorBgLayout: '#F7F8FC',
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

export const darkTheme = {
  token: {
    ...lightTheme.token,
    colorPrimary: '#A797FA',
    colorLink: '#A797FA',
    colorLinkHover: '#CBC0FF',
    colorInfo: '#A797FA',
    colorInfoBg: '#112A31',
    colorInfoBorder: '#265D6C',
    colorSuccessBg: '#162312',
    colorSuccessBorder: '#315424',
    colorWarningBg: '#2B2111',
    colorWarningBorder: '#594214',
    colorErrorBg: '#2C1618',
    colorErrorBorder: '#65282D',
    colorTextPlaceholder: 'rgba(255, 255, 255, 0.55)',
    colorBgLayout: '#111520',
    colorBgContainer: '#1B2030',
    colorBgElevated: '#23293B',
    colorBorder: '#2C3244',
    colorBorderSecondary: '#2C3244',
    colorText: '#FFFFFF',
    colorTextSecondary: 'rgba(255, 255, 255, 0.65)',
    colorTextTertiary: 'rgba(255, 255, 255, 0.45)'
  },
  components: {
    ...sharedComponents,
    Table: {
      ...sharedComponents.Table,
      borderColor: '#2C3244',
      headerBg: '#23293B',
      headerColor: 'rgba(255, 255, 255, 0.65)',
      rowHoverBg: '#252A3D'
    }
  }
}
