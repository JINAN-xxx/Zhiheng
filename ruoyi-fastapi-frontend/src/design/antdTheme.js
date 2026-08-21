export const componentSize = 'middle'

const sharedComponents = {
  Button: {
    borderRadius: 4,
    controlHeight: 36,
    fontWeight: 500,
  },
  Card: {
    borderRadiusLG: 8,
    boxShadowTertiary: 'none',
  },
  Drawer: {
    paddingLG: 20,
  },
  Input: {
    borderRadius: 4,
    controlHeight: 36,
  },
  Menu: {
    itemBorderRadius: 4,
    itemHeight: 34,
  },
  Table: {
    borderColor: '#E4E6E6',
    headerBg: '#F7F9F9',
    headerColor: '#697070',
    rowHoverBg: '#F1FAFC',
  },
}

export const lightTheme = {
  token: {
    colorPrimary: '#3996AE',
    colorInfo: '#3996AE',
    colorSuccess: '#389E0D',
    colorWarning: '#D48806',
    colorError: '#CF1322',
    colorBgLayout: '#F5F7F7',
    colorBgContainer: '#FFFFFF',
    colorBgElevated: '#FFFFFF',
    colorBorder: '#E4E6E6',
    colorBorderSecondary: '#EEF0F0',
    colorText: '#151616',
    colorTextSecondary: '#697070',
    colorTextTertiary: '#8A9090',
    borderRadius: 4,
    borderRadiusLG: 8,
    controlHeight: 36,
    fontFamily: "system-ui, 'Noto Sans SC', 'HarmonyOS Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  },
  components: sharedComponents,
}

export const darkTheme = {
  token: {
    ...lightTheme.token,
    colorPrimary: '#5FAEC2',
    colorInfo: '#5FAEC2',
    colorBgLayout: '#0C0D0D',
    colorBgContainer: '#151616',
    colorBgElevated: '#1E1F1F',
    colorBorder: '#323333',
    colorBorderSecondary: '#252727',
    colorText: '#F5F7F7',
    colorTextSecondary: '#D7D9D9',
    colorTextTertiary: '#979999',
  },
  components: {
    ...sharedComponents,
    Table: {
      ...sharedComponents.Table,
      borderColor: '#323333',
      headerBg: '#1E1F1F',
      headerColor: '#D7D9D9',
      rowHoverBg: '#16333A',
    },
  },
}
