import { defineConfig, enforceTdd } from '@nizos/probity'

export default defineConfig({
  rules: [
    {
      files: ['src/**/*.py'],
      rules: [enforceTdd({ maxEvents: 30 })],
    },
  ],
})
