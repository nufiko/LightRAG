// Fixture module for codegraph JavaScript extractor tests.

const util = require('./util')
const { helper } = require('./helper')
import defaultFn from './esmlib'

class Animal {
  speak() {
    return 'generic sound'
  }
}

class Dog extends Animal {
  constructor(name) {
    super()
    this.name = name
  }

  speak() {
    util.log(this.name)
    return this._bark()
  }

  _bark() {
    return 'woof'
  }
}

function run() {
  const d = new Dog('rex')
  d.speak()
  defaultFn()
}

const run2 = () => {
  run()
}

module.exports = { run, run2 }
