export const componentSize = 'middle'

const sharedComponents = {
  Button: {
    borderRadius: 6,
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
    borderRadius: 6,
    controlHeight: 36,
  },
  Menu: {
    itemBorderRadius: 6,
    itemHeight: 34,
  },
  Table: {
    borderColor: '#EEF0F0',
    headerBg: '#FBFCFC',
    headerColor: '#697070',
    rowHoverBg: '#F7FBFD',
  },
}

export const lightTheme = {
  token: {
    colorPrimary: '#046A82',
    colorInfo: '#1890FF',
    colorSuccess: '#52C41A',
    colorWarning: '#FAAD14',
    colorError: '#FF4D4F',
    colorBgLayout: '#F5F7F7',
    colorBgContainer: '#FFFFFF',
    colorBgElevated: '#FFFFFF',
    colorBorder: '#EEF0F0',
    colorBorderSecondary: '#E4E6E6',
    colorText: '#151616',
    colorTextSecondary: '#697070',
    colorTextTertiary: '#979999',
    borderRadius: 6,
    borderRadiusLG: 8,
    controlHeight: 36,
    fontFamily: "system-ui, 'Noto Sans SC', 'HarmonyOS Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  },
  components: sharedComponents,
}

export const darkTheme = {
  token: {
    ...lightTheme.token,
    colorPrimary: '#4A9FB8',
    colorInfo: '#4A9FB8',
    colorBgLayout: '#030303',
    colorBgContainer: '#1F1F1F',
    colorBgElevated: '#262626',
    colorBorder: '#323333',
    colorBorderSecondary: '#323333',
    colorText: '#FFFFFF',
    colorTextSecondary: 'rgba(255, 255, 255, 0.65)',
    colorTextTertiary: 'rgba(255, 255, 255, 0.45)',
  },
  components: {
    ...sharedComponents,
    Table: {
      ...sharedComponents.Table,
      borderColor: '#323333',
      headerBg: '#262626',
      headerColor: 'rgba(255, 255, 255, 0.65)',
      rowHoverBg: '#1F1F1F',
    },
  },
}
