// Fixture module for codegraph TypeScript extractor tests.

import { helper } from './utils'
import type { Config } from './config'

export interface Pettable {
  pet(): void
}

interface Namable extends Pettable {
  name: string
}

export abstract class Animal {
  protected noises: number = 0
  abstract speak(): string
}

export class Dog extends Animal implements Namable {
  constructor(public name: string) {
    super()
  }

  speak(): string {
    return this.barkStyle()
  }

  private barkStyle(): string {
    helper()
    return 'woof'
  }

  pet(): void {
    this.noises += 1
  }
}

export const run = (): void => {
  const d = new Dog('rex')
  d.speak()
  helper()
}

export const run2 = function () {
  run()
}

export enum Kind {
  Good,
  Bad,
}
